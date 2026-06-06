from django.apps import AppConfig


class CommerceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.commerce"
    label = "commerce"

    def ready(self) -> None:
        # Import handlers so their @on_event registrations run.
        from . import handlers  # noqa: F401
