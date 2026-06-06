"""Models for commerce (commerce.md §1-3).

V1-AVS owned admin-seeded products and the buyer ProductOrder state machine. The
generic payment Order lives in apps/payments; wallet debits live in apps/economy.
Money fields are Decimal(18, 4) (explicit kwargs so the django-stubs plugin reads
the signature).

Week 17 (V2) adds the buyer-facing catalog: Category, ShopBanner, and the extra
Product/SellerStore metadata the shop endpoints expose. Week 18 adds the buyer
cart (CartItem) and consolidated ShippingAddress. Week 19 adds seller onboarding
(SellerApplication → approved → SellerStore management). Refunds land in W20.
"""

from __future__ import annotations

from django.db.models import (
    CASCADE,
    PROTECT,
    SET_NULL,
    BooleanField,
    CharField,
    DateTimeField,
    DecimalField,
    ForeignKey,
    Index,
    JSONField,
    PositiveIntegerField,
    SlugField,
    TextField,
    UniqueConstraint,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class Category(AbstractBaseModel):
    """Shop product category. Reconciles with the legacy public categories source.

    Mobile keeps a synthetic "All" category (`id=null`); that is a serialization
    concern, not a stored row (see services.list_categories).
    """

    name = CharField(max_length=120)
    slug = SlugField(max_length=120, unique=True)
    sort_order = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "commerce_category"
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return f"Category({self.slug})"


class ShopBanner(AbstractBaseModel):
    """A merchandised banner on the shop home. Admin-managed."""

    PRODUCT = "product"
    CATEGORY = "category"
    EXTERNAL = "external"
    ACTION_TYPE = [(PRODUCT, PRODUCT), (CATEGORY, CATEGORY), (EXTERNAL, EXTERNAL)]

    title = CharField(max_length=200)
    description = TextField(blank=True)
    cover_image_url = URLField(blank=True)
    action_type = CharField(max_length=20, choices=ACTION_TYPE, default=PRODUCT)
    action_target = CharField(max_length=300, blank=True)  # product/category id, slug, or URL
    sort_order = PositiveIntegerField(default=0)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "commerce_shop_banner"
        ordering = ["sort_order", "-created_at"]

    def __str__(self) -> str:
        return f"ShopBanner({self.title})"


class SellerStore(AbstractBaseModel):
    """A seller's storefront. Admin-seeded in V1-AVS (seller onboarding is V2)."""

    owner_user_id = UUIDField(db_index=True)
    slug = CharField(max_length=100, unique=True)
    name = CharField(max_length=200)
    description = TextField(blank=True)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "commerce_seller_store"

    def __str__(self) -> str:
        return f"SellerStore({self.slug})"


class Product(AbstractBaseModel):
    """A purchasable product. Admin-seeded in V1-AVS; seller-managed from V2."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    STATUS = [(DRAFT, DRAFT), (ACTIVE, ACTIVE), (ARCHIVED, ARCHIVED)]

    store = ForeignKey(SellerStore, on_delete=PROTECT, related_name="products")
    category = ForeignKey(
        Category, on_delete=SET_NULL, null=True, blank=True, related_name="products"
    )
    title = CharField(max_length=300)
    slug = SlugField(max_length=300, blank=True, db_index=True)
    description = TextField(blank=True)
    cover_image_url = URLField(blank=True)
    price_amount = DecimalField(max_digits=18, decimal_places=4)
    price_currency = CharField(max_length=20)  # USD | THB | MP | MC | THB-LTT | ...
    # Map of currency -> stringified amount, e.g. {"MP": "3000.0000", "MC": "30.0000"}.
    alternate_prices = JSONField(default=dict, blank=True)
    stock = PositiveIntegerField(default=0)
    is_physical = BooleanField(default=False)
    status = CharField(max_length=20, choices=STATUS, default=ACTIVE)
    view_count = PositiveIntegerField(default=0)

    class Meta:
        db_table = "commerce_product"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["status", "created_at"], name="idx_product_status_created"),
        ]

    def __str__(self) -> str:
        return f"Product({self.title})"


class ProductOrder(AbstractBaseModel):
    """Buyer order for a product. Pays via apps/payments; settles via OrderPaid."""

    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    SHIPPING = "shipping"
    COMPLETED = "completed"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    STATUS = [
        (PENDING_PAYMENT, PENDING_PAYMENT),
        (PAID, PAID),
        (SHIPPING, SHIPPING),
        (COMPLETED, COMPLETED),
        (SETTLED, SETTLED),
        (CANCELLED, CANCELLED),
    ]

    order_no = CharField(max_length=64, unique=True)
    buyer_user_id = UUIDField(db_index=True)
    product = ForeignKey(Product, on_delete=PROTECT, related_name="orders")
    store = ForeignKey(SellerStore, on_delete=PROTECT, related_name="orders")

    # Snapshots (decoupled from the live product/address).
    product_snapshot = JSONField(default=dict)
    shipping_address_snapshot = JSONField(null=True, blank=True)

    quantity = PositiveIntegerField()
    currency = CharField(max_length=20)
    subtotal = DecimalField(max_digits=18, decimal_places=4)
    platform_fee = DecimalField(max_digits=18, decimal_places=4)
    seller_receivable = DecimalField(max_digits=18, decimal_places=4)

    status = CharField(max_length=20, choices=STATUS, default=PENDING_PAYMENT)
    payment_provider = CharField(max_length=20)
    payment_asset = CharField(max_length=20)  # currency charged in
    # lbc | ltt | ... (blockchain pay). Nullable so the AddField migration stays
    # backwards-compatible (no NOT NULL backfill), but defaults to "" so rows
    # created either path are consistent.
    blockchain_network = CharField(max_length=20, blank=True, null=True, default="")
    payment_order_no = CharField(max_length=64, blank=True)  # linked payments.Order

    expires_at = DateTimeField(null=True, blank=True)
    paid_at = DateTimeField(null=True, blank=True)
    shipped_at = DateTimeField(null=True, blank=True)
    completed_at = DateTimeField(null=True, blank=True)
    cancelled_at = DateTimeField(null=True, blank=True)
    cancel_reason = TextField(blank=True)
    idempotency_key = CharField(max_length=128, unique=True)

    class Meta:
        db_table = "commerce_product_order"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["status", "created_at"], name="idx_porder_status_created"),
            Index(fields=["store", "status"], name="idx_porder_store_status"),
        ]

    def __str__(self) -> str:
        return f"ProductOrder({self.order_no}, {self.status})"


class ProductShipment(AbstractBaseModel):
    """Shipment tracking for a paid order — commerce.md §8. One per order."""

    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    SHIPMENT_STATUS = [(PENDING, PENDING), (IN_TRANSIT, IN_TRANSIT), (DELIVERED, DELIVERED)]

    order = ForeignKey(ProductOrder, on_delete=CASCADE, related_name="shipments")
    carrier = CharField(max_length=120)
    tracking_number = CharField(max_length=200, blank=True)
    tracking_url = URLField(blank=True)
    shipment_status = CharField(max_length=20, choices=SHIPMENT_STATUS, default=IN_TRANSIT)
    shipped_note = TextField(blank=True)
    estimated_delivery = DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "commerce_product_shipment"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"ProductShipment(order={self.order_id}, {self.shipment_status})"


class RefundRequest(AbstractBaseModel):
    """A buyer's refund request against a paid/shipping/completed order — §3, §11.

    REQUESTED → (admin) APPROVED → REFUNDED, or REQUESTED → REJECTED. At most one
    active (requested/approved) request per order.
    """

    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUNDED = "refunded"
    STATUS = [
        (REQUESTED, REQUESTED),
        (APPROVED, APPROVED),
        (REJECTED, REJECTED),
        (REFUNDED, REFUNDED),
    ]

    order = ForeignKey(ProductOrder, on_delete=PROTECT, related_name="refund_requests")
    buyer_user_id = UUIDField(db_index=True)
    status = CharField(max_length=20, choices=STATUS, default=REQUESTED)
    reason = TextField(blank=True)
    requested_amount = DecimalField(max_digits=18, decimal_places=4)
    currency = CharField(max_length=20)
    admin_note = TextField(blank=True)
    resolved_at = DateTimeField(null=True, blank=True)
    resolved_by = UUIDField(null=True, blank=True)

    class Meta:
        db_table = "commerce_refund_request"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["order", "status"], name="idx_refund_order_status"),
        ]

    def __str__(self) -> str:
        return f"RefundRequest(order={self.order_id}, {self.status})"


class CartItem(AbstractBaseModel):
    """A product in a buyer's persistent (DB-backed) cart — commerce.md §2.

    One row per (user, product); adding the same product twice is a no-op (the
    UNIQUE constraint makes POST /cart idempotent without an idempotency key).
    """

    user_id = UUIDField(db_index=True)
    product = ForeignKey(Product, on_delete=CASCADE, related_name="cart_items")

    class Meta:
        db_table = "commerce_cart_item"
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(fields=["user_id", "product"], name="uq_cart_user_product"),
        ]

    def __str__(self) -> str:
        return f"CartItem(user={self.user_id}, product={self.product_id})"


class ShippingAddress(AbstractBaseModel):
    """A buyer's shipping address — commerce.md §10.

    Consolidates the two divergent legacy shapes into one. Orders snapshot the
    address at purchase time, so a hard delete here never loses order history.
    """

    user_id = UUIDField(db_index=True)
    recipient_name = CharField(max_length=200)
    phone = CharField(max_length=40, blank=True)
    street_address = CharField(max_length=500)
    city = CharField(max_length=120)
    state = CharField(max_length=120, blank=True)
    postal_code = CharField(max_length=40, blank=True)
    country = CharField(max_length=2)  # ISO 3166-1 alpha-2
    is_default = BooleanField(default=False)

    class Meta:
        db_table = "commerce_shipping_address"
        ordering = ["-is_default", "-created_at"]
        indexes = [
            Index(fields=["user_id", "is_default"], name="idx_ship_user_default"),
        ]

    def __str__(self) -> str:
        return f"ShippingAddress({self.recipient_name}, user={self.user_id})"


class SellerApplication(AbstractBaseModel):
    """A user's request to become a seller — commerce.md §5.

    At most one active (pending or approved) application per user; admin approval
    flips the user's is_seller flag (via identity) and unlocks store creation.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS = [(PENDING, PENDING), (APPROVED, APPROVED), (REJECTED, REJECTED)]

    user_id = UUIDField(db_index=True)
    status = CharField(max_length=20, choices=STATUS, default=PENDING)
    business_name = CharField(max_length=300)
    tax_id = CharField(max_length=100, blank=True)
    reason = TextField(blank=True)
    reviewed_at = DateTimeField(null=True, blank=True)
    reviewed_by = UUIDField(null=True, blank=True)
    rejection_reason = TextField(blank=True)

    class Meta:
        db_table = "commerce_seller_application"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["user_id", "status"], name="idx_sellerapp_user_status"),
        ]

    def __str__(self) -> str:
        return f"SellerApplication({self.user_id}, {self.status})"
