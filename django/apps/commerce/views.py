"""Views for commerce (commerce.md §1, §3). Parse → call service → return."""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from libs.idempotency import idempotent
from libs.jwt_auth.permissions import IsAdmin
from libs.pagination.cursor import CursorPagination

from . import services
from .serializers import (
    AddToCartSerializer,
    CancelOrderSerializer,
    CreateOrderSerializer,
    CreateSellerProductSerializer,
    CreateShippingAddressSerializer,
    CreateStoreSerializer,
    RejectApplicationSerializer,
    RequestRefundSerializer,
    ResolveQRSerializer,
    ResolveRefundSerializer,
    ShipOrderSerializer,
    SubmitSellerApplicationSerializer,
    UpdateSellerProductSerializer,
    UpdateShippingAddressSerializer,
    UpdateStoreSerializer,
)


def _uid(request: Request) -> str:
    return str(request.user.id)


# ---------------------------------------------------------------------------
# Shop catalog (buyer-facing, public) — commerce.md §1
# ---------------------------------------------------------------------------


class ShopBannerListView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(services.list_banners())


class ShopCategoryListView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(services.list_categories())


class ShopProductListView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        qs = services.products_queryset(
            category=request.query_params.get("category"),
            q=request.query_params.get("q"),
            seller_id=request.query_params.get("seller_id"),
        )
        paginator = CursorPagination()
        # DRF types `ordering` as str, but CursorPagination accepts a field tuple.
        paginator.ordering = services.product_ordering(  # type: ignore[assignment]
            request.query_params.get("ordering")
        )
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.serialize_products(list(page)))


class ShopProductDetailView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request, product_id: str) -> Response:
        return Response(services.get_product(product_id=product_id))


class OrderListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.orders_queryset(
            user_id=_uid(request), status=request.query_params.get("status")
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_order(o) for o in page])

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = request.META.get("HTTP_IDEMPOTENCY_KEY") or request.headers.get(
            "Idempotency-Key", ""
        )
        shipping = data.get("shipping_address_id")
        result = services.create_order(
            user_id=_uid(request),
            product_id=str(data["product_id"]),
            quantity=data["quantity"],
            payment_provider=data["payment_provider"],
            payment_asset=data["payment_asset"],
            blockchain_network=data.get("blockchain_network", ""),
            shipping_address_id=str(shipping) if shipping else None,
            idempotency_key=idempotency_key,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, order_no: str) -> Response:
        return Response(services.get_order(order_no=order_no, user_id=_uid(request)))


class OrderCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        serializer = CancelOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.cancel_order(
                order_no=order_no,
                user_id=_uid(request),
                reason=serializer.validated_data.get("reason", ""),
            )
        )


# ---------------------------------------------------------------------------
# Cart — commerce.md §2
# ---------------------------------------------------------------------------


class CartView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.cart_queryset(user_id=_uid(request))
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.serialize_cart_items(list(page)))

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = AddToCartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.add_to_cart(
            user_id=_uid(request), product_id=str(serializer.validated_data["product_id"])
        )
        return Response(result, status=status.HTTP_201_CREATED)


class CartItemView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request: Request, item_id: str) -> Response:
        services.remove_from_cart(user_id=_uid(request), item_id=item_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CartCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.cart_count(user_id=_uid(request)))


# ---------------------------------------------------------------------------
# Shipping addresses — commerce.md §10
# ---------------------------------------------------------------------------


class ShippingAddressListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.list_addresses(user_id=_uid(request)))

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateShippingAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.create_address(user_id=_uid(request), **serializer.validated_data)
        return Response(result, status=status.HTTP_201_CREATED)


class ShippingAddressDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, address_id: str) -> Response:
        return Response(services.get_address(user_id=_uid(request), address_id=address_id))

    def patch(self, request: Request, address_id: str) -> Response:
        serializer = UpdateShippingAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_address(
            user_id=_uid(request), address_id=address_id, **serializer.validated_data
        )
        return Response(result)

    def delete(self, request: Request, address_id: str) -> Response:
        services.delete_address(user_id=_uid(request), address_id=address_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Seller onboarding — commerce.md §5, §6
# ---------------------------------------------------------------------------


class SellerApplicationView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = SubmitSellerApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.submit_seller_application(
            user_id=_uid(request), **serializer.validated_data
        )
        return Response(result, status=status.HTTP_201_CREATED)


class SellerApplicationMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.get_my_application(user_id=_uid(request)))


class SellerApplicationApproveView(APIView):
    permission_classes = [IsAdmin]

    @idempotent
    def post(self, request: Request, application_id: str) -> Response:
        return Response(
            services.approve_seller_application(
                application_id=application_id, admin_id=_uid(request)
            )
        )


class SellerApplicationRejectView(APIView):
    permission_classes = [IsAdmin]

    @idempotent
    def post(self, request: Request, application_id: str) -> Response:
        serializer = RejectApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.reject_seller_application(
                application_id=application_id,
                admin_id=_uid(request),
                reason=serializer.validated_data.get("reason", ""),
            )
        )


class StoreMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(services.get_my_store(user_id=_uid(request)))

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateStoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.create_store(user_id=_uid(request), **serializer.validated_data)
        return Response(result, status=status.HTTP_201_CREATED)

    def patch(self, request: Request) -> Response:
        serializer = UpdateStoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.update_store(user_id=_uid(request), **serializer.validated_data)
        return Response(result)


# ---------------------------------------------------------------------------
# Seller product management — commerce.md §7
# ---------------------------------------------------------------------------


class SellerProductListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.seller_products_queryset(user_id=_uid(request))
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_owned_product(p) for p in page])

    @idempotent
    def post(self, request: Request) -> Response:
        serializer = CreateSellerProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        category_id = data.pop("category_id", None)
        result = services.create_seller_product(
            user_id=_uid(request),
            category_id=str(category_id) if category_id else None,
            **data,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class SellerProductDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, product_id: str) -> Response:
        return Response(services.get_seller_product(user_id=_uid(request), product_id=product_id))

    def patch(self, request: Request, product_id: str) -> Response:
        serializer = UpdateSellerProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "category_id" in data:
            cid = data.pop("category_id")
            data["category_id"] = str(cid) if cid else None
        result = services.update_seller_product(
            user_id=_uid(request), product_id=product_id, **data
        )
        return Response(result)

    def delete(self, request: Request, product_id: str) -> Response:
        services.archive_seller_product(user_id=_uid(request), product_id=product_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Seller order management + fulfillment — commerce.md §8, §3
# ---------------------------------------------------------------------------


class SellerOrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = services.seller_orders_queryset(
            user_id=_uid(request), status=request.query_params.get("status")
        )
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([services.serialize_order(o) for o in page])


class SellerOrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, order_no: str) -> Response:
        return Response(services.get_seller_order(user_id=_uid(request), order_no=order_no))


class SellerOrderShipView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        serializer = ShipOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.ship_order(
            user_id=_uid(request), order_no=order_no, **serializer.validated_data
        )
        return Response(result)


class OrderConfirmReceivedView(APIView):
    permission_classes = [IsAuthenticated]

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        return Response(services.confirm_received(user_id=_uid(request), order_no=order_no))


class OrderTrackingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, order_no: str) -> Response:
        return Response(services.get_tracking(user_id=_uid(request), order_no=order_no))


# ---------------------------------------------------------------------------
# Refunds — commerce.md §3, §12
# ---------------------------------------------------------------------------


class RefundRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, order_no: str) -> Response:
        return Response(services.list_refunds(user_id=_uid(request), order_no=order_no))

    @idempotent
    def post(self, request: Request, order_no: str) -> Response:
        serializer = RequestRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.request_refund(
            user_id=_uid(request),
            order_no=order_no,
            reason=data.get("reason", ""),
            requested_amount=data.get("requested_amount"),
        )
        return Response(result, status=status.HTTP_201_CREATED)


class RefundApproveView(APIView):
    permission_classes = [IsAdmin]

    @idempotent
    def post(self, request: Request, refund_id: str) -> Response:
        serializer = ResolveRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.approve_refund(
                refund_id=refund_id,
                admin_id=_uid(request),
                admin_note=serializer.validated_data.get("admin_note", ""),
            )
        )


class RefundRejectView(APIView):
    permission_classes = [IsAdmin]

    @idempotent
    def post(self, request: Request, refund_id: str) -> Response:
        serializer = ResolveRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            services.reject_refund(
                refund_id=refund_id,
                admin_id=_uid(request),
                admin_note=serializer.validated_data.get("admin_note", ""),
            )
        )


class RefundCompleteView(APIView):
    permission_classes = [IsAdmin]

    @idempotent
    def post(self, request: Request, refund_id: str) -> Response:
        return Response(services.complete_refund(refund_id=refund_id, admin_id=_uid(request)))


# ---------------------------------------------------------------------------
# QR resolution + public storefront — commerce.md §4, §9
# ---------------------------------------------------------------------------


class PaymentQRResolveView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        serializer = ResolveQRSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.resolve_qr(qr_payload=serializer.validated_data["qr_payload"]))


class PublicStoreView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request, store_slug: str) -> Response:
        return Response(services.get_public_store(store_slug=store_slug))


class PublicStoreProductsView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request: Request, store_slug: str) -> Response:
        qs = services.public_store_products_queryset(store_slug=store_slug)
        paginator = CursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(services.serialize_products(list(page)))
