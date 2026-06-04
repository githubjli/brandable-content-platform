"""JWKS endpoint URLs."""

from django.urls import path

from .views import JWKSView

urlpatterns = [
    path("jwks.json", JWKSView.as_view(), name="jwks"),
]
