import os
import warnings

from django.core.exceptions import ImproperlyConfigured


DEFAULT_DEV_SECRET_KEY = "django-insecure-(9!qv-hdc*!id3x$0i*@)opjwzdl@$y-(cv+0zo14!5tm!2ys!"

# Every secret key this repository ships as a placeholder. All of them are
# public, so the production guard must refuse each one, not only the settings
# fallback above.
KNOWN_DEV_SECRET_KEYS = frozenset({
    DEFAULT_DEV_SECRET_KEY,
    # docker-compose.yml fallback
    "dev-only-insecure-key-change-in-production",
    # backend/.env.example placeholder
    "change-me-for-production",
})


# ── AI provider ──────────────────────────────────────────────────────────────
# One provider serves every AI feature. Which one is decided here and nowhere
# else, so the settings defaults and the runtime client can never disagree.
AI_PROVIDER_KEY_VARS = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}

# Default model for each job, per provider. Any of them can be overridden with
# the matching environment variable (see backend/.env.example).
AI_MODEL_DEFAULTS = {
    "openai": {
        "AI_MODEL_FAST": "gpt-6-luna",                # short utility calls, high volume
        "AI_MODEL_VISION": "gpt-6.2-sol",             # finding items in room photos
        "AI_MODEL_EMBEDDING": "text-embedding-3-small",
        "CONCIERGE_MODEL_REASONING": "gpt-6.2-sol",   # multi-step assistant turns
        "CONCIERGE_MODEL_FAST": "gpt-6.1-terra",      # simple assistant replies
    },
    "gemini": {
        "AI_MODEL_FAST": "gemini-flash-latest",
        "AI_MODEL_VISION": "gemini-flash-latest",
        "AI_MODEL_EMBEDDING": "gemini-embedding-001",
        "CONCIERGE_MODEL_REASONING": "gemini-pro-latest",
        "CONCIERGE_MODEL_FAST": "gemini-flash-latest",
    },
}


def ai_provider(environ=None) -> str:
    """The configured AI provider: "openai", "gemini", or "" when there is none.

    AI_PROVIDER names one explicitly. Left unset, OpenAI is used when
    OPENAI_API_KEY is present and Gemini otherwise. A provider only counts
    when its key is set, so a typo in AI_PROVIDER switches AI off instead of
    silently falling through to the other account.
    """
    environ = os.environ if environ is None else environ

    def has_key(provider):
        return any(str(environ.get(name, "")).strip() for name in AI_PROVIDER_KEY_VARS[provider])

    chosen = str(environ.get("AI_PROVIDER", "")).strip().lower()
    if chosen:
        return chosen if chosen in AI_PROVIDER_KEY_VARS and has_key(chosen) else ""
    for provider in ("openai", "gemini"):
        if has_key(provider):
            return provider
    return ""


def ai_model_default(setting_name, environ=None) -> str:
    """The model a setting falls back to: the active provider's default.

    With no provider configured the Gemini names are kept, which is what
    these settings held before a second provider existed.
    """
    return AI_MODEL_DEFAULTS[ai_provider(environ) or "gemini"][setting_name]


def is_default_secret_key(secret_key: str) -> bool:
    """True when the key is one of the published development placeholders.

    The "django-insecure-" prefix is what `django-admin startproject` stamps
    on a generated key to mark it as not for production, so any key carrying
    it counts as a development key too.
    """
    return secret_key in KNOWN_DEV_SECRET_KEYS or secret_key.startswith("django-insecure-")


def media_upload_origins(bucket: str, region: str, endpoint_url: str) -> list[str]:
    """Origins a presigned S3 PUT can point at, for the CSP connect-src.

    Mirrors how boto3 addresses the bucket in listings/views/uploads.py:
    - a custom endpoint (Cloudflare R2, MinIO) is used as given, path style;
    - on AWS a DNS-compatible bucket is addressed as a subdomain of the
      global endpoint, which may redirect to the regional one, so both are
      listed;
    - a bucket name containing dots cannot be a TLS subdomain and falls back
      to path style on the regional (or global) endpoint.
    """
    bucket = (bucket or "").strip()
    if not bucket:
        return []
    endpoint_url = (endpoint_url or "").strip()
    if endpoint_url:
        from urllib.parse import urlsplit

        parts = urlsplit(endpoint_url)
        if parts.scheme and parts.netloc:
            return [f"{parts.scheme}://{parts.netloc}"]
        return []
    region = (region or "").strip() or "us-east-1"
    if "." in bucket:
        return ["https://s3.amazonaws.com", f"https://s3.{region}.amazonaws.com"]
    return [f"https://{bucket}.s3.amazonaws.com", f"https://{bucket}.s3.{region}.amazonaws.com"]


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    """int() an env var, tolerating an unset-but-present empty value.

    Hosting dashboards inject declared-but-unfilled variables as empty strings.
    A bare int("") raises at settings-import time, so the whole service fails
    to boot because an optional value was left blank.
    """
    raw = os.getenv(name, "")
    raw = raw.strip() if raw else ""
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        warnings.warn(
            f"{name}={raw!r} is not an integer; falling back to {default}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return int(default)


def env_float(name, default):
    """float() an env var, tolerating an unset-but-present empty value."""
    raw = os.getenv(name, "")
    raw = raw.strip() if raw else ""
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        warnings.warn(
            f"{name}={raw!r} is not a number; falling back to {default}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return float(default)


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

    if not secret_key or is_default_secret_key(secret_key):
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

    # Soft warnings: the app boots and works, but these will bite real users
    # (password resets never arrive; uploads vanish on redeploy). Warnings, not
    # errors, so a staging deploy without email/S3 is still possible.
    if "console" in email_backend or not email_host:
        warnings.warn(
            "Production email is not configured: set EMAIL_HOST/EMAIL_HOST_USER/"
            "EMAIL_HOST_PASSWORD (see the Deployment section of README.md). Password resets and "
            "handoff reminders will not be delivered.",
            RuntimeWarning,
            stacklevel=2,
        )
    if not media_remote:
        warnings.warn(
            "Media storage is the local filesystem: uploaded photos will be lost "
            "on redeploy on ephemeral hosts. Set AWS_S3_BUCKET_NAME (S3 or "
            "Cloudflare R2; see the Deployment section of README.md).",
            RuntimeWarning,
            stacklevel=2,
        )
