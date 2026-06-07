from django.apps import AppConfig


class MembershipConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.membership"
    label = "membership"

    def ready(self) -> None:
        # Import handlers so their @on_event registrations run.
        from . import handlers  # noqa: F401
