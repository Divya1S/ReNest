from django.apps import AppConfig


class RiskConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "risk"
    verbose_name = "Risk Intelligence"

    def ready(self) -> None:
        # Register event handlers once the app registry is loaded.
        from .services import events  # noqa: F401
