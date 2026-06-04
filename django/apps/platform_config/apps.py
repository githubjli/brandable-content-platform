from django.apps import AppConfig


class PlatformConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.platform_config"
    label = "platform_config"

    def ready(self) -> None:
        from libs.telemetry import setup_telemetry

        setup_telemetry()
