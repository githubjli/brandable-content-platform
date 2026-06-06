"""URL patterns for commerce (commerce.md §1, §2, §3, §5, §6, §10). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    # Shop catalog (buyer-facing, public) — §1
    path("commerce/shop/banners", views.ShopBannerListView.as_view(), name="commerce-shop-banners"),
    path(
        "commerce/shop/categories",
        views.ShopCategoryListView.as_view(),
        name="commerce-shop-categories",
    ),
    path(
        "commerce/shop/products",
        views.ShopProductListView.as_view(),
        name="commerce-shop-products",
    ),
    path(
        "commerce/shop/products/<uuid:product_id>",
        views.ShopProductDetailView.as_view(),
        name="commerce-shop-product-detail",
    ),
    # Cart — §2
    path("commerce/cart", views.CartView.as_view(), name="commerce-cart"),
    path("commerce/cart/count", views.CartCountView.as_view(), name="commerce-cart-count"),
    path("commerce/cart/<uuid:item_id>", views.CartItemView.as_view(), name="commerce-cart-item"),
    # Shipping addresses — §10
    path(
        "commerce/shipping-addresses",
        views.ShippingAddressListView.as_view(),
        name="commerce-shipping-addresses",
    ),
    path(
        "commerce/shipping-addresses/<uuid:address_id>",
        views.ShippingAddressDetailView.as_view(),
        name="commerce-shipping-address-detail",
    ),
    # Seller onboarding — §5, §6
    path(
        "commerce/seller-applications",
        views.SellerApplicationView.as_view(),
        name="commerce-seller-applications",
    ),
    path(
        "commerce/seller-applications/me",
        views.SellerApplicationMeView.as_view(),
        name="commerce-seller-application-me",
    ),
    path(
        "commerce/seller-applications/<uuid:application_id>/approve",
        views.SellerApplicationApproveView.as_view(),
        name="commerce-seller-application-approve",
    ),
    path(
        "commerce/seller-applications/<uuid:application_id>/reject",
        views.SellerApplicationRejectView.as_view(),
        name="commerce-seller-application-reject",
    ),
    path("commerce/store/me", views.StoreMeView.as_view(), name="commerce-store-me"),
    # Seller product management — §7
    path(
        "commerce/store/me/products",
        views.SellerProductListCreateView.as_view(),
        name="commerce-seller-products",
    ),
    path(
        "commerce/store/me/products/<uuid:product_id>",
        views.SellerProductDetailView.as_view(),
        name="commerce-seller-product-detail",
    ),
    # Seller order management — §8
    path(
        "commerce/store/me/orders",
        views.SellerOrderListView.as_view(),
        name="commerce-seller-orders",
    ),
    path(
        "commerce/store/me/orders/<str:order_no>",
        views.SellerOrderDetailView.as_view(),
        name="commerce-seller-order-detail",
    ),
    path(
        "commerce/store/me/orders/<str:order_no>/ship",
        views.SellerOrderShipView.as_view(),
        name="commerce-seller-order-ship",
    ),
    # Product orders — §3
    path("commerce/orders", views.OrderListCreateView.as_view(), name="commerce-orders"),
    path(
        "commerce/orders/<str:order_no>",
        views.OrderDetailView.as_view(),
        name="commerce-order-detail",
    ),
    path(
        "commerce/orders/<str:order_no>/cancel",
        views.OrderCancelView.as_view(),
        name="commerce-order-cancel",
    ),
    path(
        "commerce/orders/<str:order_no>/confirm-received",
        views.OrderConfirmReceivedView.as_view(),
        name="commerce-order-confirm-received",
    ),
    path(
        "commerce/orders/<str:order_no>/tracking",
        views.OrderTrackingView.as_view(),
        name="commerce-order-tracking",
    ),
    # Refunds — §3, §12
    path(
        "commerce/orders/<str:order_no>/refund-requests",
        views.RefundRequestView.as_view(),
        name="commerce-refund-requests",
    ),
    path(
        "commerce/refund-requests/<uuid:refund_id>/approve",
        views.RefundApproveView.as_view(),
        name="commerce-refund-approve",
    ),
    path(
        "commerce/refund-requests/<uuid:refund_id>/reject",
        views.RefundRejectView.as_view(),
        name="commerce-refund-reject",
    ),
    path(
        "commerce/refund-requests/<uuid:refund_id>/complete",
        views.RefundCompleteView.as_view(),
        name="commerce-refund-complete",
    ),
    # QR resolution — §4
    path(
        "commerce/payment-qr/resolve",
        views.PaymentQRResolveView.as_view(),
        name="commerce-payment-qr-resolve",
    ),
    # Public storefront — §9
    path(
        "public/stores/<str:store_slug>",
        views.PublicStoreView.as_view(),
        name="commerce-public-store",
    ),
    path(
        "public/stores/<str:store_slug>/products",
        views.PublicStoreProductsView.as_view(),
        name="commerce-public-store-products",
    ),
]
