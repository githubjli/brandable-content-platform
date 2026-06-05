from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identity"
    label = "identity"

    def ready(self) -> None:
        # Import handlers so their @on_event registrations run.
        from . import handlers  # noqa: F401
