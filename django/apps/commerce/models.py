"""Models for commerce (commerce.md §3, V1-AVS slice).

V1-AVS owns admin-seeded products and the buyer ProductOrder state machine. The
generic payment Order lives in apps/payments; wallet debits live in apps/economy.
Money fields are Decimal(18, 4) (explicit kwargs so the django-stubs plugin reads
the signature). Full marketplace (cart, seller mgmt, shipping, refunds) is V2.
"""

from __future__ import annotations

from django.db.models import (
    PROTECT,
    BooleanField,
    CharField,
    DateTimeField,
    DecimalField,
    ForeignKey,
    Index,
    JSONField,
    PositiveIntegerField,
    TextField,
    URLField,
    UUIDField,
)

from libs.errors.base_model import AbstractBaseModel


class SellerStore(AbstractBaseModel):
    """A seller's storefront. Admin-seeded in V1-AVS (seller onboarding is V2)."""

    owner_user_id = UUIDField(db_index=True)
    slug = CharField(max_length=100, unique=True)
    name = CharField(max_length=200)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "commerce_seller_store"

    def __str__(self) -> str:
        return f"SellerStore({self.slug})"


class Product(AbstractBaseModel):
    """A purchasable product. Admin-seeded in V1-AVS."""

    store = ForeignKey(SellerStore, on_delete=PROTECT, related_name="products")
    title = CharField(max_length=300)
    cover_image_url = URLField(blank=True)
    price_amount = DecimalField(max_digits=18, decimal_places=4)
    price_currency = CharField(max_length=20)  # USD | MP | MC | THB-LTT | ...
    stock = PositiveIntegerField(default=0)
    is_physical = BooleanField(default=False)
    is_active = BooleanField(default=True)

    class Meta:
        db_table = "commerce_product"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Product({self.title})"


class ProductOrder(AbstractBaseModel):
    """Buyer order for a product. Pays via apps/payments; settles via OrderPaid."""

    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    CANCELLED = "cancelled"
    STATUS = [
        (PENDING_PAYMENT, PENDING_PAYMENT),
        (PAID, PAID),
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
    payment_order_no = CharField(max_length=64, blank=True)  # linked payments.Order

    expires_at = DateTimeField(null=True, blank=True)
    paid_at = DateTimeField(null=True, blank=True)
    cancelled_at = DateTimeField(null=True, blank=True)
    cancel_reason = TextField(blank=True)
    idempotency_key = CharField(max_length=128, unique=True)

    class Meta:
        db_table = "commerce_product_order"
        ordering = ["-created_at"]
        indexes = [
            Index(fields=["status", "created_at"], name="idx_porder_status_created"),
        ]

    def __str__(self) -> str:
        return f"ProductOrder({self.order_no}, {self.status})"
