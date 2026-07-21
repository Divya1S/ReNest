import os
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Warning, register

from .env import DEFAULT_DEV_SECRET_KEY

_SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
_DEFAULT_POSTGRES_PASSWORD = "postgres"


@register()
def dormcycle_runtime_checks(app_configs, **kwargs):
    checks = []

    # E000 — dev secret key in production blocks `manage.py check --deploy`
    if not settings.DEBUG and settings.SECRET_KEY == DEFAULT_DEV_SECRET_KEY:
        checks.append(
            Error(
                "ReNest is running with the default development secret key.",
                hint="Set DJANGO_SECRET_KEY to a cryptographically random value before deploying.",
                id="dormcycle.E000",
            )
        )

    # E001 — SMTP backend with no password means outbound email will fail silently
    if not settings.DEBUG and settings.EMAIL_BACKEND == _SMTP_BACKEND:
        if not settings.EMAIL_HOST_PASSWORD:
            checks.append(
                Error(
                    "EMAIL_HOST_PASSWORD is empty but EMAIL_BACKEND is set to SMTP.",
                    hint=(
                        "Set EMAIL_HOST_PASSWORD (and EMAIL_HOST_USER) to your SMTP credentials, "
                        "or switch EMAIL_BACKEND to the console backend for local testing."
                    ),
                    id="dormcycle.E001",
                )
            )

    # W001 — APP_BASE_URL missing from CSRF trusted origins
    if settings.APP_BASE_URL and settings.APP_BASE_URL not in settings.CSRF_TRUSTED_ORIGINS:
        checks.append(
            Warning(
                "APP_BASE_URL is not present in DJANGO_CSRF_TRUSTED_ORIGINS.",
                hint="Add the deployed frontend origin to DJANGO_CSRF_TRUSTED_ORIGINS so auth bootstrap works reliably.",
                id="dormcycle.W001",
            )
        )

    # W002 — media directory missing
    media_root = Path(settings.MEDIA_ROOT)
    if not media_root.exists():
        checks.append(
            Warning(
                "MEDIA_ROOT does not exist yet.",
                hint="Create the media directory or mount persistent storage before handling uploads.",
                id="dormcycle.W002",
            )
        )

    # W003 — default Postgres password in production is a weak credential
    if not settings.DEBUG and not getattr(settings, "USE_SQLITE", False):
        if os.getenv("POSTGRES_PASSWORD", _DEFAULT_POSTGRES_PASSWORD) == _DEFAULT_POSTGRES_PASSWORD:
            checks.append(
                Warning(
                    "POSTGRES_PASSWORD is still set to the default value 'postgres'.",
                    hint="Set POSTGRES_PASSWORD to a strong unique password in your production environment.",
                    id="dormcycle.W003",
                )
            )

    return checks
