"""URL patterns for membership (membership.md V2). Mounted under api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("membership/plans", views.PlanListView.as_view(), name="membership-plans"),
    path("membership/me", views.MembershipMeView.as_view(), name="membership-me"),
    path("membership/orders", views.MembershipOrderCreateView.as_view(), name="membership-orders"),
]
