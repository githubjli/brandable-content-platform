"""Admin URL patterns for audit (audit.md §7).

Mounted under api/v1/ by config/urls.py.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("admin/audit", views.AuditListView.as_view(), name="audit-list"),
    path("admin/audit/<uuid:audit_id>", views.AuditDetailView.as_view(), name="audit-detail"),
]
