import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional helper for local env files
    load_dotenv = None

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

from .env import (
    DEFAULT_DEV_SECRET_KEY,
    env_bool,
    env_float,
    env_int,
    env_list,
    validate_environment,
)

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEFAULT_DEV_SECRET_KEY)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    # localhost first: APP_BASE_URL falls back to the first entry, and emailed
    # links must open on the same origin as the browser session (Vite prints
    # localhost:5173, so that's where the session cookie lives in dev).
    "http://localhost:5173,http://127.0.0.1:5173",
)
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    ",".join(CSRF_TRUSTED_ORIGINS),
)
CORS_ALLOW_CREDENTIALS = True
CORS_URLS_REGEX = r"^/api/.*$"

# Render injects the public hostname of a web service; trusting it here makes a
# blueprint deploy work before any domain-specific env vars are edited.
_RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
_RENDER_ORIGIN = f"https://{_RENDER_HOST}" if _RENDER_HOST else ""
if _RENDER_HOST:
    if _RENDER_HOST not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_RENDER_HOST)
    for _origins in (CSRF_TRUSTED_ORIGINS, CORS_ALLOWED_ORIGINS):
        if _RENDER_ORIGIN not in _origins:
            _origins.append(_RENDER_ORIGIN)

# Comma-separated list of allowed email domains for registration.
# Empty (default) = no restriction. Example: "usc.edu,ucla.edu"
CAMPUS_EMAIL_DOMAINS: list[str] = env_list("CAMPUS_EMAIL_DOMAINS", "")
APP_BASE_URL = os.getenv(
    "APP_BASE_URL",
    _RENDER_ORIGIN or (CSRF_TRUSTED_ORIGINS[0] if CSRF_TRUSTED_ORIGINS else "http://127.0.0.1:5173"),
).rstrip("/")

# Shared secret for POST /api/internal/maintenance/ (scheduled jobs without a
# Celery beat process, e.g. driven by a GitHub Actions cron). Unset = disabled.
MAINTENANCE_TOKEN = os.getenv("MAINTENANCE_TOKEN", "").strip()

validate_environment(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    allowed_hosts=ALLOWED_HOSTS,
    cors_allowed_origins=CORS_ALLOWED_ORIGINS,
    csrf_trusted_origins=CSRF_TRUSTED_ORIGINS,
    email_backend=os.getenv("EMAIL_BACKEND", ""),
    email_host=os.getenv("EMAIL_HOST", ""),
    media_remote=bool(os.getenv("AWS_S3_BUCKET_NAME", "").strip()),
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "accounts",
    "listings",
    "hubs",
    "concierge",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves collected static assets (admin, API docs) straight from
    # the Django process — no separate web server needed in production.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "dormcycle.middleware.ContentSecurityPolicyMiddleware",
    "dormcycle.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dormcycle.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "dormcycle.wsgi.application"

USE_SQLITE = env_bool("USE_SQLITE") or (
    "test" in sys.argv and not env_bool("USE_POSTGRES_FOR_TESTS")
)

if USE_SQLITE:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
elif os.getenv("DATABASE_URL", ""):
    # Managed-hosting convention (Render/Railway/Heroku): one URL instead of
    # discrete POSTGRES_* vars. Parsed with stdlib — no extra dependency.
    from urllib.parse import parse_qs, unquote, urlsplit

    _db_url = urlsplit(os.getenv("DATABASE_URL", ""))
    # Hosted Postgres (Neon, Supabase, Render) advertises TLS via the query
    # string; pass the libpq options through instead of silently dropping them.
    _db_options = {
        key: values[-1]
        for key, values in parse_qs(_db_url.query).items()
        if key in {"sslmode", "sslrootcert", "options", "connect_timeout", "channel_binding", "application_name"}
    }
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(_db_url.path.lstrip("/")),
            "USER": unquote(_db_url.username or ""),
            "PASSWORD": unquote(_db_url.password or ""),
            "HOST": _db_url.hostname or "",
            "PORT": str(_db_url.port or 5432),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": _db_options,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "dormcycle"),
            "USER": os.getenv("POSTGRES_USER", "postgres"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Los_Angeles"

USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── File storage ─────────────────────────────────────────────────────────────
# Static: WhiteNoise with compression + far-future caching.
# Media: local disk by default; setting AWS_S3_BUCKET_NAME switches user
# uploads (listing photos, room scans, receipts) to S3-compatible storage —
# required on hosts with ephemeral filesystems. Works with AWS S3 and
# Cloudflare R2 (set AWS_S3_ENDPOINT_URL for R2/MinIO). Thumbnails and AI
# detection read through the storage API, so they work on either backend.
AWS_S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME", "")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Non-manifest variant: compressed + cacheable without making collectstatic
    # fail on third-party assets that reference missing source maps.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# ── Single-service frontend serving ──────────────────────────────────────────
# When the built SPA is present (production single-service deploys), WhiteNoise
# serves its files at the URL root (/assets/*, /images/*, /sw.js, /manifest…)
# and a catch-all view returns index.html for client-side routes. In dev the
# dist is usually absent and Vite serves the frontend — nothing changes.
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", str(BASE_DIR.parent / "frontend" / "dist")))
SERVE_FRONTEND = (FRONTEND_DIST / "index.html").is_file()
if SERVE_FRONTEND:
    WHITENOISE_ROOT = FRONTEND_DIST

# One canonical media base URL, derived once and reused by the CSP middleware,
# the presigned-upload endpoint and the docs. Previously three modules read
# three different env var names, so the CSP never allowed the bucket that
# actually served the photos.
_S3_CDN_DOMAIN = os.getenv("AWS_S3_CDN_DOMAIN", "").strip().rstrip("/")
_S3_ENDPOINT = os.getenv("AWS_S3_ENDPOINT_URL", "").strip().rstrip("/")
if not AWS_S3_BUCKET_NAME:
    MEDIA_CDN_BASE_URL = ""
elif _S3_CDN_DOMAIN:
    MEDIA_CDN_BASE_URL = (
        _S3_CDN_DOMAIN if _S3_CDN_DOMAIN.startswith("http") else f"https://{_S3_CDN_DOMAIN}"
    )
elif _S3_ENDPOINT:
    # S3-compatible providers (Cloudflare R2, MinIO) address the bucket as a path.
    MEDIA_CDN_BASE_URL = f"{_S3_ENDPOINT}/{AWS_S3_BUCKET_NAME}"
else:
    MEDIA_CDN_BASE_URL = (
        f"https://{AWS_S3_BUCKET_NAME}.s3.{os.getenv('AWS_S3_REGION_NAME', 'us-east-1')}.amazonaws.com"
    )

if AWS_S3_BUCKET_NAME:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_S3_BUCKET_NAME,
            "region_name": os.getenv("AWS_S3_REGION_NAME", "us-east-1"),
            # R2 / MinIO compatibility; None = real AWS endpoints
            "endpoint_url": os.getenv("AWS_S3_ENDPOINT_URL") or None,
            # e.g. media.renest.app — serves media through your CDN domain
            "custom_domain": os.getenv("AWS_S3_CDN_DOMAIN") or None,
            "default_acl": None,
            "file_overwrite": False,
            # Public bucket/CDN → clean URLs; set to 1 for private buckets
            "querystring_auth": env_bool("AWS_S3_QUERYSTRING_AUTH", False),
        },
    }

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 25)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS")
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL")
# A hung SMTP connection must never pin a Celery worker — cap every send.
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "ReNest <noreply@renest.app>")
PASSWORD_RESET_TIMEOUT = 3600  # 1 hour

SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_HTTPONLY = False  # JS must read the CSRF token to send it in headers
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT")
# Tell Django the original request was HTTPS when behind a TLS-terminating proxy
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if not DEBUG else None
)
# HSTS — 0 in dev; set DJANGO_SECURE_HSTS_SECONDS=31536000 in production
SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD")
X_FRAME_OPTIONS = "DENY"

# ── Sentry ────────────────────────────────────────────────────────────────────
# Set SENTRY_DSN in production. When unset, Sentry is fully disabled.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if _SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        integrations=[DjangoIntegration()],
        environment=os.getenv("DJANGO_ENVIRONMENT", "development" if DEBUG else "production"),
        traces_sample_rate=env_float("SENTRY_TRACES_SAMPLE_RATE", 0.1),
        profiles_sample_rate=env_float("SENTRY_PROFILES_SAMPLE_RATE", 0.0),
        send_default_pii=False,
    )

# ── API schema (drf-spectacular) ──────────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    "TITLE": "ReNest API",
    "DESCRIPTION": (
        "Campus move-out rescue marketplace. "
        "Authenticated via session cookie; obtain a session with POST /api/auth/login/."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/",
    "ENUM_NAME_OVERRIDES": {
        "ListingCategoryEnum": "listings.models.Listing.Category",
        "ListingStatusEnum": "listings.models.Listing.Status",
        "ReservationStatusEnum": "listings.models.Reservation.Status",
        "RoomScanSessionStatusEnum": "listings.models.RoomScanSession.Status",
        "RescueRequestStatusEnum": "listings.models.RescueRequest.Status",
        "MoveOutTaskStatusEnum": "listings.models.MoveOutTask.Status",
        "MoveOutTaskCategoryEnum": "listings.models.MoveOutTask.Category",
        "RoomScanItemTriageStatusEnum": "listings.models.RoomScanItemDraft.TriageStatus",
    },
}

# ── Logging ───────────────────────────────────────────────────────────────────
# JSON logs in production; human-readable in dev.
# Force JSON with DJANGO_JSON_LOGS=1 (useful in CI/staging).
_JSON_LOGS = env_bool("DJANGO_JSON_LOGS", not DEBUG)
_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {
            "()": "dormcycle.middleware.RequestIdFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "fmt": "%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
        },
        "verbose": {
            "format": "[{asctime}] {levelname} {name} rid={request_id} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "json" if _JSON_LOGS else "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            # Don't spray SQL in logs; raise to DEBUG explicitly if needed.
            "level": os.getenv("DJANGO_DB_LOG_LEVEL", "WARNING"),
            "propagate": False,
        },
        "dormcycle": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        "accounts": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        "listings": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
        "hubs": {
            "handlers": ["console"],
            "level": _LOG_LEVEL,
            "propagate": False,
        },
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PARSER_CLASSES": [
        "dormcycle.parsers.StrictJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "dormcycle.pagination.StandardPagination",
    "PAGE_SIZE": 24,
    # No global throttle — applied per-view only where needed.
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "login": "10/min",
        "register": "5/min",
        "password_reset": "5/hour",
        "listing_create": "20/hour",
        "ai_detect": "10/hour",
        "reservation_create": "10/hour",
        "push_subscribe": "10/day",
        "concierge": "40/hour",
        "campus_onboard": "5/hour",
    },
    # Anonymous throttles key on the client IP. Behind one reverse proxy
    # (Render, Fly, a single nginx) the client is the last X-Forwarded-For hop;
    # with no proxy (dev) the header is untrusted and REMOTE_ADDR is used.
    "NUM_PROXIES": env_int("DJANGO_NUM_PROXIES", 0 if DEBUG else 1),
}

# ── Platform AI (Google Gemini) ──────────────────────────────────────────────
# Marketplace AI features (room-scan detection, moderation pre-screen, rescue
# matching, category suggestions, listing copywriting) share one provider with
# the concierge: Gemini via GEMINI_API_KEY. All features degrade gracefully
# when the key is absent.
AI_MODEL_FAST = os.getenv("AI_MODEL_FAST", "gemini-flash-latest")
AI_MODEL_VISION = os.getenv("AI_MODEL_VISION", "gemini-flash-latest")
AI_MODEL_EMBEDDING = os.getenv("AI_MODEL_EMBEDDING", "gemini-embedding-001")

# ── Nest Concierge (AI assistant, Google Gemini) ─────────────────────────────
# Fully additive feature: with no GEMINI_API_KEY (or CONCIERGE_ENABLED=0) the
# endpoints answer with a graceful "offline" payload and the rest of the app
# is unaffected. The semantic router sends simple conversational queries to
# the FAST model and multi-step tasks to the REASONING model.
CONCIERGE_ENABLED = env_bool("CONCIERGE_ENABLED", True)
CONCIERGE_MODEL_REASONING = os.getenv("CONCIERGE_MODEL_REASONING", "gemini-pro-latest")
CONCIERGE_MODEL_FAST = os.getenv("CONCIERGE_MODEL_FAST", "gemini-flash-latest")

# ── Cache ─────────────────────────────────────────────────────────────────────
# Set REDIS_URL in production (e.g. redis://localhost:6379/1).
# Falls back to a local-memory cache so development works without Redis.
_REDIS_URL = os.getenv("REDIS_URL", "")
if _REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _REDIS_URL,
            "OPTIONS": {
                "db": "1",
            },
            "KEY_PREFIX": "dormcycle",
            "TIMEOUT": 300,  # 5 min default TTL; overridden per call-site
        }
    }
    # Sessions: Redis-backed reads with the database as the source of truth,
    # so a restarting or LRU-evicting Redis (free tiers) never logs users out.
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
    SESSION_CACHE_ALIAS = "default"
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dormcycle-dev",
        }
    }

# ── Celery ────────────────────────────────────────────────────────────────────
# Use Redis when available; fall back to a synchronous in-process backend for dev.
_CELERY_BROKER = os.getenv("CELERY_BROKER_URL", _REDIS_URL or "")
if _CELERY_BROKER:
    CELERY_BROKER_URL = _CELERY_BROKER
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", _CELERY_BROKER)
else:
    # No Redis in dev — run tasks eagerly (inline, no worker needed)
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # fair dispatch for long-running tasks
# Reject tasks when a worker is killed mid-execution so they are requeued
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_ACKS_LATE = True

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    # Fire handoff reminders every 30 minutes (deduped per-reservation via Notification)
    "handoff-reminders": {
        "task": "listings.send_handoff_reminder_emails",
        "schedule": crontab(minute="*/30"),
    },
    # Expire overdue listings every 15 minutes
    "expire-listings": {
        "task": "listings.expire_listings",
        "schedule": crontab(minute="*/15"),
    },
    # Bump emails for stale listings — once daily at 09:00 campus time
    "bump-emails": {
        "task": "listings.send_bump_emails",
        "schedule": crontab(hour=9, minute=0),
    },
    # Phase 16 — sweep confirmed reservations that passed 48h without resolution
    "sweep-stale-confirmations": {
        "task": "listings.sweep_stale_confirmations",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # Phase 18 — demand forecast (Sunday midnight)
    "demand-forecast": {
        "task": "listings.recompute_demand_forecast",
        "schedule": crontab(hour=0, minute=0, day_of_week="sunday"),
    },
    # Phase 18 — completion rate recompute (Monday 03:00)
    "completion-rates": {
        "task": "listings.recompute_completion_rates",
        "schedule": crontab(hour=3, minute=0, day_of_week="monday"),
    },
    # Phase 20 — weekly campus digest (Sunday 08:00)
    "weekly-campus-digest": {
        "task": "listings.weekly_campus_digest",
        "schedule": crontab(hour=8, minute=0, day_of_week="sunday"),
    },
    # Phase 25 — saved search alerts (every 15 min)
    "check-saved-searches": {
        "task": "listings.check_saved_searches",
        "schedule": crontab(minute="*/15"),
    },
    # Phase 25 — refresh trending cache (every 30 min)
    "refresh-trending-cache": {
        "task": "listings.refresh_trending_cache",
        "schedule": crontab(minute="*/30"),
    },
    # Phase 26 — flush quiet-hours push queue (every 5 min)
    "flush-quiet-queue": {
        "task": "listings.flush_quiet_queue",
        "schedule": crontab(minute="*/5"),
    },
}
