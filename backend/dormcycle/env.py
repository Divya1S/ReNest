import os
import warnings

from django.core.exceptions import ImproperlyConfigured


DEFAULT_DEV_SECRET_KEY = "django-insecure-(9!qv-hdc*!id3x$0i*@)opjwzdl@$y-(cv+0zo14!5tm!2ys!"


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


_DEV_ORIGINS = frozenset({
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://localhost",
    "http://localhost:5173",
})


def validate_environment(
    *,
    debug,
    secret_key,
    allowed_hosts,
    cors_allowed_origins,
    csrf_trusted_origins,
    email_backend="",
    email_host="",
    media_remote=True,
):
    if debug:
        return

    if not secret_key or secret_key == DEFAULT_DEV_SECRET_KEY:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set to a non-default value when DJANGO_DEBUG=0."
        )

    if not allowed_hosts:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS must include at least one hostname when DJANGO_DEBUG=0."
        )

    if not cors_allowed_origins or all(o in _DEV_ORIGINS for o in cors_allowed_origins):
        raise ImproperlyConfigured(
            "CORS_ALLOWED_ORIGINS must be set to your production domain(s) when DJANGO_DEBUG=0. "
            "Do not ship with localhost origins."
        )

    if not csrf_trusted_origins or all(o in _DEV_ORIGINS for o in csrf_trusted_origins):
        raise ImproperlyConfigured(
            "DJANGO_CSRF_TRUSTED_ORIGINS must be set to your production domain(s) when DJANGO_DEBUG=0. "
            "Do not ship with localhost origins."
        )

    # Soft warnings — the app boots and works, but these will bite real users
    # (password resets never arrive; uploads vanish on redeploy). Warnings, not
    # errors, so a staging deploy without email/S3 is still possible.
    if "console" in email_backend or not email_host:
        warnings.warn(
            "Production email is not configured: set EMAIL_HOST/EMAIL_HOST_USER/"
            "EMAIL_HOST_PASSWORD (see docs/deployment.md). Password resets and "
            "handoff reminders will not be delivered.",
            RuntimeWarning,
            stacklevel=2,
        )
    if not media_remote:
        warnings.warn(
            "Media storage is the local filesystem: uploaded photos will be lost "
            "on redeploy on ephemeral hosts. Set AWS_S3_BUCKET_NAME (S3 or "
            "Cloudflare R2 — see docs/deployment.md).",
            RuntimeWarning,
            stacklevel=2,
        )
