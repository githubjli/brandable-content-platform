"""Service layer for commerce (commerce.md §1, §3).

Commerce orchestrates the purchase chain: it owns the ProductOrder, delegates the
payment to apps/payments, and (for wallet payment) the debit to apps/economy. It
never touches Stripe/blockchain adapters or wallet rows directly. Payment
settlement flows back uniformly through the `payments.OrderPaid` event, handled in
apps/commerce/handlers.py.

The §1 block adds the buyer-facing shop catalog (banners, categories, products);
it is read-only and batches owner cards via identity.public_profiles.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from libs.errors.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
    ValidationError,
)

from .models import (
    CartItem,
    Category,
    Product,
    ProductOrder,
    ProductShipment,
    RefundRequest,
    SellerApplication,
    SellerStore,
    ShippingAddress,
    ShopBanner,
)

logger = logging.getLogger(__name__)
_CENT = Decimal("0.0001")
_SUPPORTED_PROVIDERS = {"stripe", "wallet", "blockchain"}
_WALLET_ASSETS = {"MP", "MC"}


# ---------------------------------------------------------------------------
# Cross-app stubs
# ---------------------------------------------------------------------------


def _emit(
    event_type: str, payload: dict, idempotency_key: str, actor_id: str | None = None
) -> None:
    try:
        from apps.events.services import emit

        emit(
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            actor_id=actor_id,
        )
    except Exception:
        logger.debug("_emit: emit failed; skipping %s", event_type)


def _record_audit(
    action: str,
    *,
    actor_id: str | None,
    target_id: str,
    target_type: str = "ProductOrder",
    after_state: dict | None = None,
) -> None:
    from apps.audit.services import record_audit

    record_audit(
        action=action,
        actor_type="user",
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        after_state=after_state,
        severity="info",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(_CENT)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _order_no() -> str:
    return f"CO-{_now():%Y%m%d}-{uuid.uuid4().hex[:12].upper()}"


def _fee_rate() -> Decimal:
    return Decimal(str(getattr(settings, "COMMERCE_PLATFORM_FEE_RATE", "0.05")))


def _compute_amounts(unit_price: Decimal, quantity: int) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = _money(unit_price * quantity)
    platform_fee = _money(subtotal * _fee_rate())
    seller_receivable = _money(subtotal - platform_fee)
    return subtotal, platform_fee, seller_receivable


def _money_obj(amount: Decimal, currency: str) -> dict[str, str]:
    return {"amount": str(amount), "currency": currency}


def serialize_order(order: ProductOrder, payment: dict | None = None) -> dict[str, Any]:
    return {
        "order_no": order.order_no,
        "product_snapshot": order.product_snapshot,
        "quantity": order.quantity,
        "amounts": {
            "subtotal": _money_obj(order.subtotal, order.currency),
            "platform_fee": _money_obj(order.platform_fee, order.currency),
            "seller_receivable": _money_obj(order.seller_receivable, order.currency),
        },
        "shipping_address_snapshot": order.shipping_address_snapshot,
        "seller_store": {
            "id": str(order.store_id),
            "slug": order.store.slug,
            "name": order.store.name,
        },
        "status": order.status,
        "payment": payment
        or {
            "provider": order.payment_provider,
            "payment_order_no": order.payment_order_no or None,
        },
        "expires_at": _iso(order.expires_at),
        "paid_at": _iso(order.paid_at),
        "qr_payload": _qr_payload(order),
        "qr_text": _qr_text(order),
        "created_at": _iso(order.created_at),
    }


# ---------------------------------------------------------------------------
# Shop catalog (buyer-facing, read-only) — commerce.md §1
# ---------------------------------------------------------------------------

# Whitelist of buyer-selectable orderings (avoids arbitrary ORDER BY injection).
# Each value is the tuple handed to the cursor paginator: the primary field plus a
# stable tiebreaker so pagination stays deterministic when the primary field ties.
_PRODUCT_ORDERINGS = {
    "-created_at": ("-created_at",),
    "created_at": ("created_at",),
    "-view_count": ("-view_count", "-created_at"),
    "price_amount": ("price_amount", "-created_at"),
    "-price_amount": ("-price_amount", "-created_at"),
}
_DEFAULT_PRODUCT_ORDERING = "-created_at"


def product_ordering(ordering: str | None) -> tuple[str, ...]:
    """Resolve a buyer ordering param to the cursor paginator's ordering tuple.

    DRF's CursorPagination ignores a queryset's own order_by and applies its own
    `ordering` attribute, so the view assigns this onto the paginator.
    """
    return _PRODUCT_ORDERINGS.get(ordering or "", _PRODUCT_ORDERINGS[_DEFAULT_PRODUCT_ORDERING])


def serialize_category(category: Category) -> dict[str, Any]:
    return {"id": str(category.id), "name": category.name, "slug": category.slug}


def serialize_banner(banner: ShopBanner) -> dict[str, Any]:
    return {
        "id": str(banner.id),
        "title": banner.title,
        "description": banner.description or None,
        "cover_image_url": banner.cover_image_url or None,
        "action_type": banner.action_type,
        "action_target": banner.action_target or None,
        "sort_order": banner.sort_order,
    }


def serialize_product(
    product: Product, *, owner: dict | None = None, detail: bool = False
) -> dict[str, Any]:
    """Buyer-facing product card. `owner` is a public profile from
    identity.public_profiles (batched by the caller to avoid N+1)."""
    alternate = {k: str(v) for k, v in (product.alternate_prices or {}).items()}
    data: dict[str, Any] = {
        "id": str(product.id),
        "title": product.title,
        "description": product.description or None,
        "price": _money_obj(product.price_amount, product.price_currency),
        "alternate_prices": alternate,
        "cover_image_url": product.cover_image_url or None,
        "stock_quantity": product.stock,
        "store": {
            "id": str(product.store_id),
            "slug": product.store.slug,
            "name": product.store.name,
            "owner": owner or {"id": str(product.store.owner_user_id), "display_name": None},
        },
        "category": serialize_category(product.category) if product.category else None,
        "status": product.status,
    }
    if detail:
        data["description_html"] = product.description or ""
        data["created_at"] = _iso(product.created_at)
        data["updated_at"] = _iso(product.updated_at)
    return data


def products_queryset(
    *,
    category: str | None = None,
    q: str | None = None,
    seller_id: str | None = None,
):
    """Active-product catalog queryset for cursor pagination (view layer)."""
    from django.db.models import Q

    qs = Product.objects.select_related("store", "category").filter(status=Product.ACTIVE)
    if category and category != "all":
        qs = qs.filter(category__slug=category)
    if seller_id:
        qs = qs.filter(store__owner_user_id=seller_id)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(slug__icontains=q))
    # Ordering is applied by the cursor paginator (see product_ordering); the
    # `ordering` arg is accepted here only to keep the filter surface in one place.
    return qs


def serialize_products(products: list[Product]) -> list[dict[str, Any]]:
    """Serialize a product page with a single batched owner lookup."""
    from apps.identity.services import public_profiles

    owners = public_profiles([str(p.store.owner_user_id) for p in products])
    return [serialize_product(p, owner=owners.get(str(p.store.owner_user_id))) for p in products]


def products_by_ids(*, product_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batched buyer-facing product cards keyed by id (active only). For cross-app
    enrichment (e.g. live product bindings). Missing/inactive ids are omitted."""
    from apps.identity.services import public_profiles

    if not product_ids:
        return {}
    products = list(
        Product.objects.select_related("store", "category").filter(
            id__in=product_ids,
            status=Product.ACTIVE,
        )
    )
    owners = public_profiles([str(p.store.owner_user_id) for p in products])
    return {
        str(p.id): serialize_product(p, owner=owners.get(str(p.store.owner_user_id)))
        for p in products
    }


def get_product(*, product_id: str) -> dict[str, Any]:
    from apps.identity.services import public_profiles

    try:
        product = Product.objects.select_related("store", "category").get(
            id=product_id, status=Product.ACTIVE
        )
    except Product.DoesNotExist:
        raise NotFoundError(code="PRODUCT_NOT_FOUND", message="Product not found.")
    owner = public_profiles([str(product.store.owner_user_id)]).get(
        str(product.store.owner_user_id)
    )
    return serialize_product(product, owner=owner, detail=True)


def list_categories() -> dict[str, Any]:
    """Active categories, prefixed with the synthetic mobile-compat "All" (id=null)."""
    categories = Category.objects.filter(is_active=True)
    results = [{"id": None, "name": "All", "slug": "all"}]
    results.extend(serialize_category(c) for c in categories)
    return {"results": results}


def list_banners() -> dict[str, Any]:
    banners = ShopBanner.objects.filter(is_active=True)
    return {"results": [serialize_banner(b) for b in banners]}


# ---------------------------------------------------------------------------
# Cart (buyer, DB-backed) — commerce.md §2
# ---------------------------------------------------------------------------


def cart_queryset(*, user_id: str):
    """Buyer cart queryset for cursor pagination (newest first)."""
    return CartItem.objects.filter(user_id=user_id).select_related(
        "product", "product__store", "product__category"
    )


def serialize_cart_items(items: list[CartItem]) -> list[dict[str, Any]]:
    """Serialize a cart page with a single batched owner lookup."""
    from apps.identity.services import public_profiles

    owners = public_profiles([str(i.product.store.owner_user_id) for i in items])
    return [
        {
            "id": str(i.id),
            "product": serialize_product(
                i.product, owner=owners.get(str(i.product.store.owner_user_id))
            ),
            "created_at": _iso(i.created_at),
        }
        for i in items
    ]


def add_to_cart(*, user_id: str, product_id: str) -> dict[str, Any]:
    """Add a product to the cart. Idempotent: re-adding is a no-op (UNIQUE)."""
    from apps.identity.services import public_profiles

    try:
        product = Product.objects.select_related("store", "category").get(
            id=product_id, status=Product.ACTIVE
        )
    except Product.DoesNotExist:
        raise NotFoundError(code="PRODUCT_NOT_FOUND", message="Product not found.")

    item, created = CartItem.objects.get_or_create(user_id=user_id, product=product)
    if created:
        _emit(
            event_type="commerce.CartItemAdded",
            payload={
                "user_id": str(user_id),
                "product_id": str(product.id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_cart_added:{item.id}",
            actor_id=str(user_id),
        )
    owner = public_profiles([str(product.store.owner_user_id)]).get(
        str(product.store.owner_user_id)
    )
    return {
        "id": str(item.id),
        "product": serialize_product(product, owner=owner),
        "created_at": _iso(item.created_at),
    }


def remove_from_cart(*, user_id: str, item_id: str) -> None:
    try:
        item = CartItem.objects.get(id=item_id, user_id=user_id)
    except CartItem.DoesNotExist:
        raise NotFoundError(code="CART_ITEM_NOT_FOUND", message="Cart item not found.")
    product_id = str(item.product_id)
    item.delete()
    _emit(
        event_type="commerce.CartItemRemoved",
        payload={
            "user_id": str(user_id),
            "product_id": product_id,
            "occurred_at": _iso(_now()),
        },
        idempotency_key=f"commerce_cart_removed:{item_id}",
        actor_id=str(user_id),
    )


def cart_count(*, user_id: str) -> dict[str, int]:
    return {"count": CartItem.objects.filter(user_id=user_id).count()}


# ---------------------------------------------------------------------------
# Shipping addresses — commerce.md §10
# ---------------------------------------------------------------------------


def serialize_address(addr: ShippingAddress) -> dict[str, Any]:
    return {
        "id": str(addr.id),
        "recipient_name": addr.recipient_name,
        "phone": addr.phone or None,
        "street_address": addr.street_address,
        "city": addr.city,
        "state": addr.state or None,
        "postal_code": addr.postal_code or None,
        "country": addr.country,
        "is_default": addr.is_default,
        "created_at": _iso(addr.created_at),
    }


def _unset_other_defaults(user_id: str, keep_id: Any) -> None:
    ShippingAddress.objects.filter(user_id=user_id, is_default=True).exclude(id=keep_id).update(
        is_default=False
    )


def list_addresses(*, user_id: str) -> dict[str, Any]:
    addrs = ShippingAddress.objects.filter(user_id=user_id)
    return {"results": [serialize_address(a) for a in addrs]}


def get_address(*, user_id: str, address_id: str) -> dict[str, Any]:
    try:
        addr = ShippingAddress.objects.get(id=address_id, user_id=user_id)
    except ShippingAddress.DoesNotExist:
        raise NotFoundError(code="SHIPPING_ADDRESS_NOT_FOUND", message="Address not found.")
    return serialize_address(addr)


def create_address(*, user_id: str, is_default: bool = False, **fields: Any) -> dict[str, Any]:
    with transaction.atomic():
        # The first address a buyer adds is their default regardless of the flag.
        is_first = not ShippingAddress.objects.filter(user_id=user_id).exists()
        addr = ShippingAddress.objects.create(
            user_id=user_id, is_default=is_default or is_first, **fields
        )
        if addr.is_default:
            _unset_other_defaults(user_id, addr.id)
    return serialize_address(addr)


def update_address(
    *, user_id: str, address_id: str, is_default: bool | None = None, **fields: Any
) -> dict[str, Any]:
    with transaction.atomic():
        try:
            addr = ShippingAddress.objects.select_for_update().get(id=address_id, user_id=user_id)
        except ShippingAddress.DoesNotExist:
            raise NotFoundError(code="SHIPPING_ADDRESS_NOT_FOUND", message="Address not found.")
        for key, value in fields.items():
            setattr(addr, key, value)
        if is_default is not None:
            addr.is_default = is_default
        addr.save()
        if addr.is_default:
            _unset_other_defaults(user_id, addr.id)
    return serialize_address(addr)


def delete_address(*, user_id: str, address_id: str) -> None:
    with transaction.atomic():
        try:
            addr = ShippingAddress.objects.select_for_update().get(id=address_id, user_id=user_id)
        except ShippingAddress.DoesNotExist:
            raise NotFoundError(code="SHIPPING_ADDRESS_NOT_FOUND", message="Address not found.")
        was_default = addr.is_default
        addr.delete()
        # Keep exactly one default: promote the most recent remaining address.
        if was_default:
            nxt = ShippingAddress.objects.filter(user_id=user_id).order_by("-created_at").first()
            if nxt is not None:
                ShippingAddress.objects.filter(id=nxt.id).update(is_default=True)


def _resolve_shipping_snapshot(
    *, user_id: str, product: Product, shipping_address_id: str | None
) -> dict[str, Any] | None:
    """Snapshot the buyer's chosen address into the order, or enforce that a
    physical product has one."""
    if shipping_address_id:
        try:
            addr = ShippingAddress.objects.get(id=shipping_address_id, user_id=user_id)
        except ShippingAddress.DoesNotExist:
            raise NotFoundError(
                code="SHIPPING_ADDRESS_NOT_FOUND", message="Shipping address not found."
            )
        return serialize_address(addr)
    if product.is_physical:
        raise ValidationError(
            code="ORDER_SHIPPING_ADDRESS_REQUIRED",
            message="A shipping address is required for physical products.",
        )
    return None


# ---------------------------------------------------------------------------
# Seller onboarding — commerce.md §5, §6
# ---------------------------------------------------------------------------

_ACTIVE_APPLICATION_STATES = (SellerApplication.PENDING, SellerApplication.APPROVED)


def serialize_application(app: SellerApplication) -> dict[str, Any]:
    return {
        "id": str(app.id),
        "user_id": str(app.user_id),
        "status": app.status,
        "business_name": app.business_name,
        "tax_id": app.tax_id or None,
        "reason": app.reason or None,
        "submitted_at": _iso(app.created_at),
        "reviewed_at": _iso(app.reviewed_at),
        "reviewed_by": str(app.reviewed_by) if app.reviewed_by else None,
        "rejection_reason": app.rejection_reason or None,
    }


def submit_seller_application(
    *, user_id: str, business_name: str, tax_id: str = "", reason: str = ""
) -> dict[str, Any]:
    with transaction.atomic():
        if SellerApplication.objects.filter(
            user_id=user_id, status__in=_ACTIVE_APPLICATION_STATES
        ).exists():
            raise ConflictError(
                code="SELLER_APPLICATION_ALREADY_EXISTS",
                message="An active seller application already exists.",
            )
        app = SellerApplication.objects.create(
            user_id=user_id, business_name=business_name, tax_id=tax_id, reason=reason
        )
        _emit(
            event_type="commerce.SellerApplicationSubmitted",
            payload={
                "application_id": str(app.id),
                "user_id": str(user_id),
                "business_name": business_name,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_seller_app_submitted:{app.id}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.seller_application.submit",
            actor_id=str(user_id),
            target_id=str(app.id),
            target_type="SellerApplication",
            after_state={"status": app.status},
        )
    return serialize_application(app)


def get_my_application(*, user_id: str) -> dict[str, Any]:
    app = SellerApplication.objects.filter(user_id=user_id).order_by("-created_at").first()
    if app is None:
        raise NotFoundError(
            code="SELLER_APPLICATION_NOT_FOUND", message="No seller application found."
        )
    return serialize_application(app)


def approve_seller_application(*, application_id: str, admin_id: str) -> dict[str, Any]:
    from apps.identity.services import mark_seller

    with transaction.atomic():
        try:
            app = SellerApplication.objects.select_for_update().get(id=application_id)
        except SellerApplication.DoesNotExist:
            raise NotFoundError(
                code="SELLER_APPLICATION_NOT_FOUND", message="Application not found."
            )
        if app.status != SellerApplication.PENDING:
            raise ConflictError(
                code="SELLER_APPLICATION_NOT_PENDING",
                message=f"Application is already {app.status}.",
            )
        app.status = SellerApplication.APPROVED
        app.reviewed_at = _now()
        app.reviewed_by = admin_id
        app.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
        mark_seller(user_id=str(app.user_id))
        _emit(
            event_type="commerce.SellerApplicationApproved",
            payload={
                "application_id": str(app.id),
                "user_id": str(app.user_id),
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_seller_app_approved:{app.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="commerce.seller_application.approve",
            actor_id=str(admin_id),
            target_id=str(app.id),
            target_type="SellerApplication",
            after_state={"status": "approved", "user_id": str(app.user_id)},
        )
    return serialize_application(app)


def reject_seller_application(
    *, application_id: str, admin_id: str, reason: str = ""
) -> dict[str, Any]:
    with transaction.atomic():
        try:
            app = SellerApplication.objects.select_for_update().get(id=application_id)
        except SellerApplication.DoesNotExist:
            raise NotFoundError(
                code="SELLER_APPLICATION_NOT_FOUND", message="Application not found."
            )
        if app.status != SellerApplication.PENDING:
            raise ConflictError(
                code="SELLER_APPLICATION_NOT_PENDING",
                message=f"Application is already {app.status}.",
            )
        app.status = SellerApplication.REJECTED
        app.reviewed_at = _now()
        app.reviewed_by = admin_id
        app.rejection_reason = reason
        app.save(
            update_fields=["status", "reviewed_at", "reviewed_by", "rejection_reason", "updated_at"]
        )
        _emit(
            event_type="commerce.SellerApplicationRejected",
            payload={
                "application_id": str(app.id),
                "user_id": str(app.user_id),
                "reason": reason,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_seller_app_rejected:{app.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="commerce.seller_application.reject",
            actor_id=str(admin_id),
            target_id=str(app.id),
            target_type="SellerApplication",
            after_state={"status": "rejected"},
        )
    return serialize_application(app)


def _store_stats(store: SellerStore) -> dict[str, Any]:
    from django.db.models import Sum

    total_products = Product.objects.filter(store=store).exclude(status=Product.ARCHIVED).count()
    total_orders = ProductOrder.objects.filter(store=store).count()
    paid = ProductOrder.objects.filter(store=store, status=ProductOrder.PAID)
    revenue = paid.aggregate(total=Sum("seller_receivable"))["total"] or Decimal("0")
    # Revenue currency is reported from the most recent paid order (single-currency
    # stores are the norm; mixed-currency reporting is a later refinement).
    last_paid = paid.order_by("-created_at").first()
    currency = last_paid.currency if last_paid is not None else "USD"
    return {
        "total_products": total_products,
        "total_orders": total_orders,
        "total_revenue": _money_obj(_money(revenue), currency),
    }


def serialize_store(store: SellerStore, *, stats: bool = False) -> dict[str, Any]:
    from apps.identity.services import public_profiles

    owner = public_profiles([str(store.owner_user_id)]).get(str(store.owner_user_id))
    data: dict[str, Any] = {
        "id": str(store.id),
        "slug": store.slug,
        "name": store.name,
        "description": store.description or None,
        "owner": owner or {"id": str(store.owner_user_id), "display_name": None},
        "is_active": store.is_active,
        "created_at": _iso(store.created_at),
        "updated_at": _iso(store.updated_at),
    }
    if stats:
        data["stats"] = _store_stats(store)
    return data


def _my_store(user_id: str) -> SellerStore | None:
    return SellerStore.objects.filter(owner_user_id=user_id).order_by("created_at").first()


def get_my_store(*, user_id: str) -> dict[str, Any]:
    store = _my_store(user_id)
    if store is None:
        raise NotFoundError(code="STORE_NOT_FOUND", message="You do not have a store.")
    return serialize_store(store, stats=True)


def create_store(*, user_id: str, slug: str, name: str, description: str = "") -> dict[str, Any]:
    with transaction.atomic():
        approved = SellerApplication.objects.filter(
            user_id=user_id, status=SellerApplication.APPROVED
        ).exists()
        if not approved:
            raise ForbiddenError(
                code="SELLER_NOT_APPROVED",
                message="An approved seller application is required to open a store.",
            )
        if SellerStore.objects.filter(owner_user_id=user_id).exists():
            raise ConflictError(code="STORE_ALREADY_EXISTS", message="You already have a store.")
        if SellerStore.objects.filter(slug=slug).exists():
            raise ConflictError(code="STORE_SLUG_TAKEN", message="That store slug is taken.")
        store = SellerStore.objects.create(
            owner_user_id=user_id, slug=slug, name=name, description=description
        )
        _emit(
            event_type="commerce.StoreCreated",
            payload={
                "store_id": str(store.id),
                "owner_user_id": str(user_id),
                "slug": slug,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_store_created:{store.id}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.store.create",
            actor_id=str(user_id),
            target_id=str(store.id),
            target_type="SellerStore",
            after_state={"slug": slug, "name": name},
        )
    return serialize_store(store, stats=True)


def update_store(*, user_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"name", "description", "is_active"}  # slug is immutable
    with transaction.atomic():
        store = (
            SellerStore.objects.select_for_update()
            .filter(owner_user_id=user_id)
            .order_by("created_at")
            .first()
        )
        if store is None:
            raise NotFoundError(code="STORE_NOT_FOUND", message="You do not have a store.")
        for key, value in fields.items():
            if key in allowed:
                setattr(store, key, value)
        store.save()
    return serialize_store(store, stats=True)


# ---------------------------------------------------------------------------
# Seller product management — commerce.md §7
# ---------------------------------------------------------------------------


def _require_store(user_id: str) -> SellerStore:
    store = _my_store(user_id)
    if store is None:
        raise NotFoundError(code="STORE_NOT_FOUND", message="You do not have a store.")
    return store


def _resolve_category(category_id: Any) -> Category | None:
    if not category_id:
        return None
    try:
        return Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        raise ValidationError(code="CATEGORY_NOT_FOUND", message="Category not found.")


def serialize_owned_product(product: Product) -> dict[str, Any]:
    """Seller-facing product (own store) — exposes draft/archived + stock + dates."""
    return {
        "id": str(product.id),
        "title": product.title,
        "description": product.description or None,
        "price": _money_obj(product.price_amount, product.price_currency),
        "alternate_prices": {k: str(v) for k, v in (product.alternate_prices or {}).items()},
        "cover_image_url": product.cover_image_url or None,
        "stock_quantity": product.stock,
        "is_physical": product.is_physical,
        "category": serialize_category(product.category) if product.category else None,
        "status": product.status,
        "created_at": _iso(product.created_at),
        "updated_at": _iso(product.updated_at),
    }


def seller_products_queryset(*, user_id: str):
    """All products (draft/active/archived) for the seller's store."""
    store = _my_store(user_id)
    if store is None:
        return Product.objects.none()
    return Product.objects.select_related("category", "store").filter(store=store)


def create_seller_product(
    *,
    user_id: str,
    title: str,
    price_amount: Any,
    price_currency: str,
    description: str = "",
    cover_image_url: str = "",
    alternate_prices: dict | None = None,
    stock_quantity: int = 0,
    is_physical: bool = False,
    category_id: str | None = None,
    status: str = Product.DRAFT,
) -> dict[str, Any]:
    if status not in (Product.DRAFT, Product.ACTIVE):
        raise ValidationError(
            code="PRODUCT_INVALID_STATUS", message="status must be draft or active."
        )
    with transaction.atomic():
        store = _require_store(user_id)
        category = _resolve_category(category_id)
        product = Product.objects.create(
            store=store,
            category=category,
            title=title,
            description=description,
            cover_image_url=cover_image_url,
            price_amount=_money(price_amount),
            price_currency=price_currency,
            alternate_prices=alternate_prices or {},
            stock=stock_quantity,
            is_physical=is_physical,
            status=status,
        )
        _emit(
            event_type="commerce.ProductCreated",
            payload={
                "product_id": str(product.id),
                "store_id": str(store.id),
                "status": status,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_product_created:{product.id}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.product.create",
            actor_id=str(user_id),
            target_id=str(product.id),
            target_type="Product",
            after_state={"title": title, "status": status},
        )
    return serialize_owned_product(product)


def _owned_product_for_update(user_id: str, product_id: str) -> Product:
    store = _require_store(user_id)
    try:
        # Lock only the product row (of="self"); category is a nullable FK, and
        # Postgres rejects FOR UPDATE on the nullable side of the outer join.
        return (
            Product.objects.select_for_update(of=("self",))
            .select_related("category", "store")
            .get(id=product_id, store=store)
        )
    except Product.DoesNotExist:
        raise NotFoundError(code="PRODUCT_NOT_FOUND", message="Product not found.")


def get_seller_product(*, user_id: str, product_id: str) -> dict[str, Any]:
    store = _require_store(user_id)
    try:
        product = Product.objects.select_related("category", "store").get(
            id=product_id, store=store
        )
    except Product.DoesNotExist:
        raise NotFoundError(code="PRODUCT_NOT_FOUND", message="Product not found.")
    return serialize_owned_product(product)


def update_seller_product(*, user_id: str, product_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {
        "title",
        "description",
        "cover_image_url",
        "price_amount",
        "price_currency",
        "alternate_prices",
        "stock_quantity",
        "is_physical",
        "status",
    }
    model_field = {"stock_quantity": "stock"}
    with transaction.atomic():
        product = _owned_product_for_update(user_id, product_id)
        if "category_id" in fields:
            product.category = _resolve_category(fields.pop("category_id"))
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "status" and value not in (
                Product.DRAFT,
                Product.ACTIVE,
                Product.ARCHIVED,
            ):
                raise ValidationError(
                    code="PRODUCT_INVALID_STATUS",
                    message="status must be draft, active, or archived.",
                )
            if key == "price_amount":
                value = _money(value)
            setattr(product, model_field.get(key, key), value)
        product.save()
        _emit(
            event_type="commerce.ProductUpdated",
            payload={"product_id": str(product.id), "occurred_at": _iso(_now())},
            idempotency_key=f"commerce_product_updated:{product.id}:{_now().timestamp()}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.product.update",
            actor_id=str(user_id),
            target_id=str(product.id),
            target_type="Product",
        )
    return serialize_owned_product(product)


def archive_seller_product(*, user_id: str, product_id: str) -> None:
    """Soft delete: status → archived (preserves order history)."""
    with transaction.atomic():
        product = _owned_product_for_update(user_id, product_id)
        if product.status == Product.ARCHIVED:
            return
        product.status = Product.ARCHIVED
        product.save(update_fields=["status", "updated_at"])
        _emit(
            event_type="commerce.ProductArchived",
            payload={"product_id": str(product.id), "occurred_at": _iso(_now())},
            idempotency_key=f"commerce_product_archived:{product.id}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.product.archive",
            actor_id=str(user_id),
            target_id=str(product.id),
            target_type="Product",
            after_state={"status": "archived"},
        )


# ---------------------------------------------------------------------------
# Seller order management + fulfillment — commerce.md §8, §3
# ---------------------------------------------------------------------------


def seller_orders_queryset(*, user_id: str, status: str | None = None):
    store = _my_store(user_id)
    if store is None:
        return ProductOrder.objects.none()
    qs = ProductOrder.objects.select_related("store").filter(store=store)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)
    return qs


def get_seller_order(*, user_id: str, order_no: str) -> dict[str, Any]:
    store = _require_store(user_id)
    try:
        order = ProductOrder.objects.select_related("store").get(order_no=order_no, store=store)
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    return serialize_order(order)


def ship_order(
    *,
    user_id: str,
    order_no: str,
    carrier: str,
    tracking_number: str = "",
    tracking_url: str = "",
    shipped_note: str = "",
) -> dict[str, Any]:
    with transaction.atomic():
        store = _require_store(user_id)
        try:
            order = (
                ProductOrder.objects.select_for_update()
                .select_related("store")
                .get(order_no=order_no, store=store)
            )
        except ProductOrder.DoesNotExist:
            raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
        if order.status != ProductOrder.PAID:
            raise ConflictError(
                code="ORDER_NOT_SHIPPABLE",
                message=f"Order in status {order.status} cannot be shipped.",
            )
        ProductShipment.objects.create(
            order=order,
            carrier=carrier,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
            shipped_note=shipped_note,
            shipment_status=ProductShipment.IN_TRANSIT,
        )
        order.status = ProductOrder.SHIPPING
        order.shipped_at = _now()
        order.save(update_fields=["status", "shipped_at", "updated_at"])
        _emit(
            event_type="commerce.OrderShipped",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(order.buyer_user_id),
                "carrier": carrier,
                "tracking_number": tracking_number or None,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_shipped:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.order.ship",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"status": "shipping", "carrier": carrier},
        )
    return serialize_order(order)


def confirm_received(*, user_id: str, order_no: str) -> dict[str, Any]:
    with transaction.atomic():
        try:
            order = (
                ProductOrder.objects.select_for_update()
                .select_related("store")
                .get(order_no=order_no, buyer_user_id=user_id)
            )
        except ProductOrder.DoesNotExist:
            raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
        if order.status != ProductOrder.SHIPPING:
            raise ConflictError(
                code="ORDER_NOT_CONFIRMABLE",
                message=f"Order in status {order.status} cannot be confirmed received.",
            )
        order.status = ProductOrder.COMPLETED
        order.completed_at = _now()
        order.save(update_fields=["status", "completed_at", "updated_at"])
        ProductShipment.objects.filter(order=order).update(
            shipment_status=ProductShipment.DELIVERED
        )
        _emit(
            event_type="commerce.OrderCompleted",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(order.buyer_user_id),
                "store_id": str(order.store_id),
                "seller_receivable": str(order.seller_receivable),
                "currency": order.currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_completed:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.order.complete",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"status": "completed"},
        )
    return serialize_order(order)


def serialize_shipment(order: ProductOrder, shipment: ProductShipment) -> dict[str, Any]:
    return {
        "order_no": order.order_no,
        "carrier": shipment.carrier,
        "tracking_number": shipment.tracking_number or None,
        "tracking_url": shipment.tracking_url or None,
        "shipment_status": shipment.shipment_status,
        "estimated_delivery": _iso(shipment.estimated_delivery),
        "last_update": _iso(shipment.updated_at),
    }


def get_tracking(*, user_id: str, order_no: str) -> dict[str, Any]:
    try:
        order = ProductOrder.objects.get(order_no=order_no, buyer_user_id=user_id)
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    shipment = ProductShipment.objects.filter(order=order).order_by("-created_at").first()
    if shipment is None:
        raise NotFoundError(code="SHIPMENT_NOT_FOUND", message="No shipment for this order yet.")
    return serialize_shipment(order, shipment)


# ---------------------------------------------------------------------------
# Refunds — commerce.md §3, §11, §12
# ---------------------------------------------------------------------------

_REFUNDABLE_ORDER_STATES = (
    ProductOrder.PAID,
    ProductOrder.SHIPPING,
    ProductOrder.COMPLETED,
)
_ACTIVE_REFUND_STATES = (RefundRequest.REQUESTED, RefundRequest.APPROVED)


def serialize_refund(refund: RefundRequest) -> dict[str, Any]:
    return {
        "id": str(refund.id),
        "order_no": refund.order.order_no,
        "status": refund.status,
        "reason": refund.reason or None,
        "requested_amount": _money_obj(refund.requested_amount, refund.currency),
        "admin_note": refund.admin_note or None,
        "resolved_at": _iso(refund.resolved_at),
        "created_at": _iso(refund.created_at),
    }


def request_refund(
    *, user_id: str, order_no: str, reason: str = "", requested_amount: Any = None
) -> dict[str, Any]:
    with transaction.atomic():
        try:
            order = ProductOrder.objects.select_for_update().get(
                order_no=order_no, buyer_user_id=user_id
            )
        except ProductOrder.DoesNotExist:
            raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
        if order.status not in _REFUNDABLE_ORDER_STATES:
            raise ConflictError(
                code="ORDER_NOT_REFUNDABLE",
                message=f"Order in status {order.status} is not refundable.",
            )
        if RefundRequest.objects.filter(order=order, status__in=_ACTIVE_REFUND_STATES).exists():
            raise ConflictError(
                code="REFUND_ALREADY_ACTIVE",
                message="An active refund request already exists for this order.",
            )
        amount = _money(requested_amount) if requested_amount is not None else order.subtotal
        if amount <= 0 or amount > order.subtotal:
            raise ValidationError(
                code="REFUND_INVALID_AMOUNT",
                message="requested_amount must be between 0 and the order subtotal.",
            )
        refund = RefundRequest.objects.create(
            order=order,
            buyer_user_id=user_id,
            reason=reason,
            requested_amount=amount,
            currency=order.currency,
        )
        _emit(
            event_type="commerce.RefundRequested",
            payload={
                "refund_id": str(refund.id),
                "order_no": order.order_no,
                "buyer_user_id": str(user_id),
                "amount": str(amount),
                "currency": order.currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_refund_requested:{refund.id}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.refund.request",
            actor_id=str(user_id),
            target_id=str(refund.id),
            target_type="RefundRequest",
            after_state={"order_no": order.order_no, "amount": str(amount)},
        )
    return serialize_refund(refund)


def list_refunds(*, user_id: str, order_no: str) -> dict[str, Any]:
    try:
        order = ProductOrder.objects.get(order_no=order_no, buyer_user_id=user_id)
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    refunds = RefundRequest.objects.select_related("order").filter(order=order)
    return {"results": [serialize_refund(r) for r in refunds]}


def _locked_refund(refund_id: str) -> RefundRequest:
    try:
        # order FK is non-null → inner join, so locking the joined rows is safe.
        return RefundRequest.objects.select_for_update().select_related("order").get(id=refund_id)
    except RefundRequest.DoesNotExist:
        raise NotFoundError(code="REFUND_NOT_FOUND", message="Refund request not found.")


def approve_refund(*, refund_id: str, admin_id: str, admin_note: str = "") -> dict[str, Any]:
    with transaction.atomic():
        refund = _locked_refund(refund_id)
        if refund.status != RefundRequest.REQUESTED:
            raise ConflictError(
                code="REFUND_NOT_PENDING", message=f"Refund is already {refund.status}."
            )
        refund.status = RefundRequest.APPROVED
        refund.admin_note = admin_note
        refund.resolved_at = _now()
        refund.resolved_by = admin_id
        refund.save(
            update_fields=["status", "admin_note", "resolved_at", "resolved_by", "updated_at"]
        )
        _emit(
            event_type="commerce.RefundApproved",
            payload={
                "refund_id": str(refund.id),
                "order_no": refund.order.order_no,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_refund_approved:{refund.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="commerce.refund.approve",
            actor_id=str(admin_id),
            target_id=str(refund.id),
            target_type="RefundRequest",
            after_state={"status": "approved"},
        )
    return serialize_refund(refund)


def reject_refund(*, refund_id: str, admin_id: str, admin_note: str = "") -> dict[str, Any]:
    with transaction.atomic():
        refund = _locked_refund(refund_id)
        if refund.status != RefundRequest.REQUESTED:
            raise ConflictError(
                code="REFUND_NOT_PENDING", message=f"Refund is already {refund.status}."
            )
        refund.status = RefundRequest.REJECTED
        refund.admin_note = admin_note
        refund.resolved_at = _now()
        refund.resolved_by = admin_id
        refund.save(
            update_fields=["status", "admin_note", "resolved_at", "resolved_by", "updated_at"]
        )
        _emit(
            event_type="commerce.RefundRejected",
            payload={
                "refund_id": str(refund.id),
                "order_no": refund.order.order_no,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_refund_rejected:{refund.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="commerce.refund.reject",
            actor_id=str(admin_id),
            target_id=str(refund.id),
            target_type="RefundRequest",
            after_state={"status": "rejected"},
        )
    return serialize_refund(refund)


def complete_refund(*, refund_id: str, admin_id: str) -> dict[str, Any]:
    """Mark an approved refund refunded; credit the buyer's wallet for wallet-paid
    orders. Stripe/blockchain provider refunds are recorded here but settled
    out-of-band."""
    with transaction.atomic():
        refund = _locked_refund(refund_id)
        if refund.status != RefundRequest.APPROVED:
            raise ConflictError(
                code="REFUND_NOT_APPROVED",
                message=f"Refund must be approved before completion (is {refund.status}).",
            )
        order = refund.order
        ledger_entry_id = None
        if order.payment_provider == "wallet":
            from apps.economy.services import credit as economy_credit

            ledger = economy_credit(
                user_id=str(order.buyer_user_id),
                currency=order.payment_asset,
                entry_type="REFUND",
                amount=refund.requested_amount,
                idempotency_key=f"product_refund:{refund.id}",
                target_type="RefundRequest",
                target_id=str(refund.id),
                note=f"Refund for order {order.order_no}",
            )
            ledger_entry_id = ledger["id"]
        refund.status = RefundRequest.REFUNDED
        refund.resolved_at = _now()
        refund.resolved_by = admin_id
        refund.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])
        _emit(
            event_type="commerce.RefundCompleted",
            payload={
                "refund_id": str(refund.id),
                "order_no": order.order_no,
                "buyer_user_id": str(order.buyer_user_id),
                "amount": str(refund.requested_amount),
                "currency": refund.currency,
                "ledger_entry_id": ledger_entry_id,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_refund_completed:{refund.id}",
            actor_id=str(admin_id),
        )
        _record_audit(
            action="commerce.refund.complete",
            actor_id=str(admin_id),
            target_id=str(refund.id),
            target_type="RefundRequest",
            after_state={"status": "refunded", "ledger_entry_id": ledger_entry_id},
        )
    return serialize_refund(refund)


# ---------------------------------------------------------------------------
# QR resolution + public storefront — commerce.md §4, §9
# ---------------------------------------------------------------------------


def _qr_payload(order: ProductOrder) -> dict[str, Any]:
    return {"v": 1, "type": "product_order", "order_no": order.order_no}


def _qr_text(order: ProductOrder) -> str:
    return f"brandable://pay?order={order.order_no}"


def resolve_qr(*, qr_payload: dict) -> dict[str, Any]:
    order_no = (qr_payload or {}).get("order_no")
    if not order_no:
        raise NotFoundError(code="QR_INVALID_OR_EXPIRED", message="QR code is invalid or expired.")
    try:
        order = ProductOrder.objects.select_related("store").get(
            order_no=order_no, status=ProductOrder.PENDING_PAYMENT
        )
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="QR_INVALID_OR_EXPIRED", message="QR code is invalid or expired.")
    if order.expires_at is not None and order.expires_at < _now():
        raise NotFoundError(code="QR_INVALID_OR_EXPIRED", message="QR code is invalid or expired.")
    snap = order.product_snapshot or {}
    return {
        "order_no": order.order_no,
        "product_title": snap.get("title"),
        "product_image_url": snap.get("cover_image_url"),
        "price": _money_obj(order.subtotal, order.currency),
        "seller_name": order.store.name,
        "payment_asset": order.payment_asset,
        "status": order.status,
        "expires_at": _iso(order.expires_at),
    }


def get_public_store(*, store_slug: str) -> dict[str, Any]:
    try:
        store = SellerStore.objects.get(slug=store_slug, is_active=True)
    except SellerStore.DoesNotExist:
        raise NotFoundError(code="STORE_NOT_FOUND", message="Store not found.")
    return serialize_store(store, stats=True)


def public_store_products_queryset(*, store_slug: str):
    try:
        store = SellerStore.objects.get(slug=store_slug, is_active=True)
    except SellerStore.DoesNotExist:
        raise NotFoundError(code="STORE_NOT_FOUND", message="Store not found.")
    return (
        Product.objects.select_related("store", "category")
        .filter(store=store, status=Product.ACTIVE)
        .order_by("-created_at")
    )


# ---------------------------------------------------------------------------
# Create / get / cancel
# ---------------------------------------------------------------------------


def create_order(
    *,
    user_id: str,
    product_id: str,
    quantity: int,
    payment_provider: str,
    payment_asset: str,
    idempotency_key: str,
    shipping_address_id: str | None = None,
    blockchain_network: str = "",
) -> dict[str, Any]:
    """Create a ProductOrder + linked payments.Order. Idempotent on idempotency_key.

    Wallet payment debits MP/MC immediately and settles the order; Stripe payment
    returns a client_secret and blockchain payment returns a pay-to address — both
    settle later (Stripe webhook / on-chain confirmation) and flip the order to
    `paid` through the `payments.OrderPaid` handler.
    """
    if quantity < 1:
        raise ValidationError(code="ORDER_INVALID_QUANTITY", message="quantity must be >= 1.")
    if payment_provider not in _SUPPORTED_PROVIDERS:
        raise ValidationError(
            code="ORDER_PROVIDER_UNSUPPORTED",
            message="payment_provider must be stripe, wallet, or blockchain.",
        )
    if payment_provider == "blockchain" and not blockchain_network:
        raise ValidationError(
            code="ORDER_NETWORK_REQUIRED",
            message="blockchain_network is required for blockchain payment.",
        )
    if not idempotency_key:
        raise ValidationError(
            code="ORDER_IDEMPOTENCY_KEY_REQUIRED", message="idempotency_key required."
        )

    existing = ProductOrder.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return serialize_order(existing)

    from apps.payments.services import create_order as payments_create_order
    from apps.payments.services import settle_wallet_order

    with transaction.atomic():
        try:
            product = (
                Product.objects.select_for_update()
                .select_related("store")
                .get(id=product_id, status=Product.ACTIVE)
            )
        except Product.DoesNotExist:
            raise NotFoundError(code="PRODUCT_NOT_FOUND", message="Product not found.")

        if payment_asset != product.price_currency:
            raise ValidationError(
                code="ORDER_ASSET_MISMATCH",
                message=f"payment_asset must be {product.price_currency} for this product.",
            )
        if payment_provider == "wallet" and payment_asset not in _WALLET_ASSETS:
            raise ValidationError(
                code="ORDER_ASSET_UNSUPPORTED", message="Wallet payment requires MP or MC."
            )

        if product.stock < quantity:
            raise UnprocessableError(
                code="STOCK_INSUFFICIENT",
                message="Not enough stock.",
                detail={"requested": quantity, "available": product.stock},
            )

        address_snapshot = _resolve_shipping_snapshot(
            user_id=str(user_id), product=product, shipping_address_id=shipping_address_id
        )

        product.stock -= quantity
        product.save(update_fields=["stock", "updated_at"])

        subtotal, platform_fee, seller_receivable = _compute_amounts(product.price_amount, quantity)
        order = ProductOrder.objects.create(
            order_no=_order_no(),
            buyer_user_id=user_id,
            product=product,
            store=product.store,
            product_snapshot={
                "id": str(product.id),
                "title": product.title,
                "cover_image_url": product.cover_image_url or None,
                "price_at_order": _money_obj(product.price_amount, product.price_currency),
            },
            shipping_address_snapshot=address_snapshot,
            quantity=quantity,
            currency=product.price_currency,
            subtotal=subtotal,
            platform_fee=platform_fee,
            seller_receivable=seller_receivable,
            payment_provider=payment_provider,
            payment_asset=payment_asset,
            blockchain_network=blockchain_network,
            expires_at=_now() + timedelta(seconds=settings.COMMERCE_ORDER_TTL_SECONDS),
            idempotency_key=idempotency_key,
        )

        payment = payments_create_order(
            user_id=str(user_id),
            business_kind="PRODUCT",
            business_ref_id=str(order.id),
            amount=subtotal,
            currency=product.price_currency,
            payment_provider=payment_provider,
            blockchain_network=blockchain_network,
            idempotency_key=f"product_order:{order.id}",
        )
        order.payment_order_no = payment["order_no"]
        order.save(update_fields=["payment_order_no", "updated_at"])

        if payment_provider == "wallet":
            from apps.economy.services import debit as economy_debit

            ledger = economy_debit(
                user_id=str(user_id),
                currency=payment_asset,
                entry_type="SPEND",
                amount=subtotal,
                idempotency_key=f"product_spend:{order.id}",
                target_type="ProductOrder",
                target_id=str(order.id),
                note=f"Product order {order.order_no}",
            )
            # Settle the payment Order → emits payments.OrderPaid (handled async).
            settle_wallet_order(order_no=order.payment_order_no, ledger_entry_id=ledger["id"])

        _emit(
            event_type="commerce.OrderCreated",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(user_id),
                "product_id": str(product.id),
                "store_id": str(order.store_id),
                "subtotal": str(subtotal),
                "currency": product.price_currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_created:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.order.create",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"order_no": order.order_no, "provider": payment_provider},
        )

    return serialize_order(order, payment=payment["payment"])


def get_order(*, order_no: str, user_id: str) -> dict[str, Any]:
    try:
        order = ProductOrder.objects.select_related("store").get(
            order_no=order_no, buyer_user_id=user_id
        )
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")
    return serialize_order(order)


def orders_queryset(*, user_id: str, status: str | None = None):
    """Buyer's orders for cursor pagination. `status` may be a comma list."""
    qs = ProductOrder.objects.select_related("store").filter(buyer_user_id=user_id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            qs = qs.filter(status__in=statuses)
    return qs


def cancel_order(*, order_no: str, user_id: str, reason: str = "") -> dict[str, Any]:
    try:
        order = (
            ProductOrder.objects.select_for_update()
            .select_related("store")
            .get(order_no=order_no, buyer_user_id=user_id)
        )
    except ProductOrder.DoesNotExist:
        raise NotFoundError(code="ORDER_NOT_FOUND", message="Order not found.")

    # V1-AVS: only unpaid orders can be cancelled (paid → refund is V2).
    if order.status != ProductOrder.PENDING_PAYMENT:
        raise ConflictError(
            code="ORDER_NOT_CANCELLABLE",
            message=f"Order in status {order.status} cannot be cancelled.",
        )

    with transaction.atomic():
        # Release the reserved stock.
        Product.objects.filter(id=order.product_id).update(stock=_stock_plus(order))
        order.status = ProductOrder.CANCELLED
        order.cancelled_at = _now()
        order.cancel_reason = reason
        order.save(update_fields=["status", "cancelled_at", "cancel_reason", "updated_at"])

        # Best-effort cancel of the linked payment order.
        if order.payment_order_no:
            try:
                from apps.payments.services import cancel_order as payments_cancel

                payments_cancel(order_no=order.payment_order_no, actor_id=str(user_id))
            except Exception:
                logger.debug("cancel_order: payment order already terminal; skipping")

        _emit(
            event_type="commerce.OrderCancelled",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(user_id),
                "reason": reason,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_cancelled:{order.order_no}",
            actor_id=str(user_id),
        )
        _record_audit(
            action="commerce.order.cancel",
            actor_id=str(user_id),
            target_id=str(order.id),
            after_state={"reason": reason},
        )
    return serialize_order(order)


def _stock_plus(order: ProductOrder):
    from django.db.models import F

    return F("stock") + order.quantity


# ---------------------------------------------------------------------------
# Settlement (called by the payments.OrderPaid handler)
# ---------------------------------------------------------------------------


def mark_order_paid(*, product_order_id: str) -> None:
    """Flip a ProductOrder to paid once its payment settled. Idempotent."""
    try:
        order = ProductOrder.objects.get(id=product_order_id)
    except ProductOrder.DoesNotExist:
        return
    if order.status == ProductOrder.PAID:
        return

    with transaction.atomic():
        order.status = ProductOrder.PAID
        order.paid_at = _now()
        order.save(update_fields=["status", "paid_at", "updated_at"])
        _emit(
            event_type="commerce.OrderPaid",
            payload={
                "order_no": order.order_no,
                "buyer_user_id": str(order.buyer_user_id),
                "store_id": str(order.store_id),
                "seller_receivable": str(order.seller_receivable),
                "currency": order.currency,
                "occurred_at": _iso(_now()),
            },
            idempotency_key=f"commerce_order_paid:{order.order_no}",
            actor_id=str(order.buyer_user_id),
        )
        _record_audit(
            action="commerce.order.paid",
            actor_id=str(order.buyer_user_id),
            target_id=str(order.id),
            after_state={"status": "paid"},
        )
