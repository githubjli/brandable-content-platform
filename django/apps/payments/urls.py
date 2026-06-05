"""URL patterns for payments (payments.md §3-4). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("payments/orders", views.OrderListView.as_view(), name="payments-order-list"),
    path(
        "payments/orders/<str:order_no>",
        views.OrderDetailView.as_view(),
        name="payments-order-detail",
    ),
    path(
        "payments/orders/<str:order_no>/verify",
        views.OrderVerifyView.as_view(),
        name="payments-order-verify",
    ),
    path(
        "payments/orders/<str:order_no>/cancel",
        views.OrderCancelView.as_view(),
        name="payments-order-cancel",
    ),
    path(
        "payments/webhooks/stripe",
        views.StripeWebhookView.as_view(),
        name="payments-webhook-stripe",
    ),
]
