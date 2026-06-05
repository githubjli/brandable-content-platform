from django.apps import AppConfig


class EconomyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.economy"
    label = "economy"

    def ready(self) -> None:
        # Import handlers so their @on_event registrations run.
        from . import handlers  # noqa: F401
