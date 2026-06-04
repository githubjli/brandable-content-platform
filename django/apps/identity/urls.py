"""URL patterns for identity.

These are mounted under api/v1/ by config/urls.py, so paths here are
relative to that prefix (e.g. "auth/register" becomes /api/v1/auth/register).
"""

from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path("auth/register", views.RegisterView.as_view(), name="identity-register"),
    path("auth/login", views.LoginView.as_view(), name="identity-login"),
    path("auth/refresh", views.RefreshView.as_view(), name="identity-refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="identity-logout"),
    path("auth/me", views.MeView.as_view(), name="identity-me"),
    path("auth/sessions", views.SessionsView.as_view(), name="identity-sessions"),
    path(
        "auth/sessions/<uuid:session_id>",
        views.SessionDetailView.as_view(),
        name="identity-session-detail",
    ),
    # Password
    path(
        "auth/password/reset/request",
        views.PasswordResetRequestView.as_view(),
        name="identity-password-reset-request",
    ),
    path(
        "auth/password/reset/confirm",
        views.PasswordResetConfirmView.as_view(),
        name="identity-password-reset-confirm",
    ),
    path(
        "auth/password/change", views.PasswordChangeView.as_view(), name="identity-password-change"
    ),
    # Account
    path("account/profile", views.ProfileView.as_view(), name="identity-profile"),
    path("account/preferences", views.PreferencesView.as_view(), name="identity-preferences"),
    # KYC
    path("account/kyc", views.KycView.as_view(), name="identity-kyc"),
    path("account/kyc/documents", views.KycDocumentView.as_view(), name="identity-kyc-documents"),
    path("account/kyc/submit", views.KycSubmitView.as_view(), name="identity-kyc-submit"),
    # Creator profile
    path(
        "account/creator-profile",
        views.CreatorProfileView.as_view(),
        name="identity-creator-profile",
    ),
    # Public users
    path(
        "public/users/<uuid:user_id>", views.PublicUserView.as_view(), name="identity-public-user"
    ),
    path("public/users/<uuid:user_id>/follow", views.FollowView.as_view(), name="identity-follow"),
    # Public creators
    path(
        "public/creators/<uuid:creator_id>",
        views.PublicCreatorView.as_view(),
        name="identity-public-creator",
    ),
    path(
        "public/creators/<uuid:creator_id>/videos",
        views.PublicCreatorVideosView.as_view(),
        name="identity-creator-videos",
    ),
]
