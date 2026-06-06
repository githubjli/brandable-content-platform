"""URL patterns for commerce (commerce.md §3, V1-AVS). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("commerce/orders", views.OrderCreateView.as_view(), name="commerce-order-create"),
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
]
