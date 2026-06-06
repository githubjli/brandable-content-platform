"""Serializers for commerce (request-body validation only)."""

from __future__ import annotations

from rest_framework import serializers


class CreateOrderSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    payment_provider = serializers.ChoiceField(choices=["stripe", "wallet", "blockchain"])
    payment_asset = serializers.CharField(max_length=20)
    blockchain_network = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    shipping_address_id = serializers.UUIDField(required=False, allow_null=True)


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class AddToCartSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()


class CreateShippingAddressSerializer(serializers.Serializer):
    recipient_name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    street_address = serializers.CharField(max_length=500)
    city = serializers.CharField(max_length=120)
    state = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    postal_code = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=2)
    is_default = serializers.BooleanField(default=False)


class UpdateShippingAddressSerializer(serializers.Serializer):
    """All fields optional (PATCH semantics)."""

    recipient_name = serializers.CharField(max_length=200, required=False)
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    street_address = serializers.CharField(max_length=500, required=False)
    city = serializers.CharField(max_length=120, required=False)
    state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    postal_code = serializers.CharField(max_length=40, required=False, allow_blank=True)
    country = serializers.CharField(max_length=2, required=False)
    is_default = serializers.BooleanField(required=False)


class SubmitSellerApplicationSerializer(serializers.Serializer):
    business_name = serializers.CharField(max_length=300)
    tax_id = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class RejectApplicationSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class CreateStoreSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=100)
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")


class UpdateStoreSerializer(serializers.Serializer):
    """All fields optional (PATCH semantics); slug is immutable."""

    name = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)


class CreateSellerProductSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    cover_image_url = serializers.URLField(required=False, allow_blank=True, default="")
    price_amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0)
    price_currency = serializers.CharField(max_length=20)
    alternate_prices = serializers.DictField(
        child=serializers.CharField(), required=False, default=dict
    )
    stock_quantity = serializers.IntegerField(min_value=0, default=0)
    is_physical = serializers.BooleanField(default=False)
    category_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=["draft", "active"], default="draft")


class UpdateSellerProductSerializer(serializers.Serializer):
    """All fields optional (PATCH semantics)."""

    title = serializers.CharField(max_length=300, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    cover_image_url = serializers.URLField(required=False, allow_blank=True)
    price_amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, required=False
    )
    price_currency = serializers.CharField(max_length=20, required=False)
    alternate_prices = serializers.DictField(child=serializers.CharField(), required=False)
    stock_quantity = serializers.IntegerField(min_value=0, required=False)
    is_physical = serializers.BooleanField(required=False)
    category_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=["draft", "active", "archived"], required=False)


class ShipOrderSerializer(serializers.Serializer):
    carrier = serializers.CharField(max_length=120)
    tracking_number = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )
    tracking_url = serializers.URLField(required=False, allow_blank=True, default="")
    shipped_note = serializers.CharField(required=False, allow_blank=True, default="")


class RequestRefundSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    requested_amount = serializers.DecimalField(
        max_digits=18, decimal_places=4, min_value=0, required=False, allow_null=True
    )


class ResolveRefundSerializer(serializers.Serializer):
    """Admin note for approve/reject."""

    admin_note = serializers.CharField(required=False, allow_blank=True, default="")


class ResolveQRSerializer(serializers.Serializer):
    qr_payload = serializers.DictField(required=True)
