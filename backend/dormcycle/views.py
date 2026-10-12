import hmac
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.csrf import csrf_failure as django_csrf_failure
from django.db import connections
from django.db.utils import OperationalError
from drf_spectacular.settings import spectacular_settings
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularSwaggerSplitView
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """CSRF_FAILURE_VIEW: answer API callers in the API's own error shape.

    The auth endpoints are CSRF-checked by Django's csrf_protect, whose stock
    failure page is HTML. The SPA reads `detail` from every error body, and
    DRF words its own CSRF rejections (authenticated requests) the same way,
    so the client sees one format whichever layer refused the request.
    """
    if request.path.startswith("/api/"):
        return JsonResponse({"detail": f"CSRF Failed: {reason}"}, status=403)
    return django_csrf_failure(request, reason=reason)


@extend_schema(exclude=True)
class HealthLiveView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


@extend_schema(exclude=True)
class HealthReadyView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        checks = {
            "database": False,
            "media_root": False,
        }
        warnings = []
        ready = True

        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = True
        except OperationalError:
            # Log the driver message server-side; the public payload must not
            # reveal hostnames or credentials embedded in the error text.
            logger.warning("Readiness probe: database check failed", exc_info=True)
            ready = False
            warnings.append("Database check failed.")

        # The local media directory only matters when uploads land on disk;
        # with object storage configured its absence is expected.
        if settings.AWS_S3_BUCKET_NAME:
            checks["media_root"] = True
        else:
            media_root = Path(settings.MEDIA_ROOT)
            checks["media_root"] = media_root.exists() and media_root.is_dir()
            if not checks["media_root"]:
                ready = False
                warnings.append("MEDIA_ROOT is missing or not a directory.")

        if settings.APP_BASE_URL not in settings.CSRF_TRUSTED_ORIGINS:
            warnings.append("APP_BASE_URL is not listed in DJANGO_CSRF_TRUSTED_ORIGINS.")

        payload = {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
            "warnings": warnings,
        }
        return Response(
            payload,
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


@extend_schema(exclude=True)
class HealthWorkerView(APIView):
    """Check that at least one Celery worker is online and responsive."""

    permission_classes = [permissions.AllowAny]

    def get(self, _request):
        # In eager mode (dev without Redis) always report healthy
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            return Response({"status": "ok", "workers": [], "mode": "eager"})

        try:
            from celery import current_app
            inspector = current_app.control.inspect(timeout=2.0)
            ping = inspector.ping() or {}
            workers = list(ping.keys())
            if not workers:
                return Response(
                    {"status": "no_workers", "workers": []},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            # Worker hostnames describe the deployment; the public answer is a count.
            if getattr(_request.user, "is_staff", False):
                return Response({"status": "ok", "workers": workers})
            return Response({"status": "ok", "worker_count": len(workers)})
        except Exception:
            # The broker error text can carry the Redis URL (host, password).
            # This endpoint is public, so keep the detail in the server log.
            logger.warning("Worker health check failed", exc_info=True)
            return Response(
                {"status": "error", "detail": "Worker status is unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


@extend_schema(exclude=True)
class HealthDlqView(APIView):
    """
    Staff-only endpoint that returns the length of the Celery dead-letter queue
    stored in Redis so ops can tell at a glance whether tasks are silently failing.
    """

    permission_classes = [permissions.IsAdminUser]

    def get(self, _request):
        from dormcycle.celery import _DLQ_KEY, redis_client

        # Eager mode: no Redis, no DLQ
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            return Response({"mode": "eager", "length": 0})

        try:
            client = redis_client()
            if client is None:
                return Response({"mode": "eager", "length": 0})
            length = client.llen(_DLQ_KEY)
            return Response({"length": length, "key": _DLQ_KEY})
        except Exception:
            logger.warning("DLQ health check failed", exc_info=True)
            return Response(
                {"status": "error", "detail": "Dead-letter queue is unreachable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


MAINTENANCE_JOB_SETS = ("tick", "daily", "weekly", "all")


def _header_bytes(value: str) -> bytes:
    """The bytes a header value had on the wire.

    WSGI and ASGI both hand header values over as latin-1 decoded text. The
    fallback only matters for callers that build a request object directly
    with characters outside latin-1 (the test client can).
    """
    try:
        return value.encode("latin-1")
    except UnicodeEncodeError:
        return value.encode("utf-8")


@extend_schema(exclude=True)
class MaintenanceRunView(APIView):
    """POST /api/internal/maintenance/: run the scheduled job sets on demand.

    Lets a free external cron (the GitHub Actions workflow in
    .github/workflows/maintenance.yml) drive listing expiry, handoff
    reminders, saved-search alerts, trending refresh and digests on hosts
    that cannot run a Celery beat process. Protected by the MAINTENANCE_TOKEN
    shared secret; the route answers 404 while the token is unset.
    """

    authentication_classes: list[Any] = []
    permission_classes = [permissions.AllowAny]

    def post(self, request: Any) -> Response:
        token = settings.MAINTENANCE_TOKEN
        if not token:
            raise Http404
        header = request.headers.get("Authorization", "")
        provided = header[7:].strip() if header.startswith("Bearer ") else ""
        # Compare bytes: compare_digest raises TypeError for a str holding
        # non-ASCII characters, which turned a junk token into a 500.
        if not provided or not hmac.compare_digest(_header_bytes(provided), token.encode("utf-8")):
            return Response({"detail": "Invalid maintenance token."}, status=status.HTTP_403_FORBIDDEN)

        payload = request.data if isinstance(request.data, dict) else {}
        jobs = payload.get("jobs", ["tick"])
        if isinstance(jobs, str):
            jobs = [jobs]
        if (
            not isinstance(jobs, list)
            or not jobs
            or any(not isinstance(job, str) or job not in MAINTENANCE_JOB_SETS for job in jobs)
        ):
            return Response(
                {"detail": f"jobs must be a non-empty list drawn from {list(MAINTENANCE_JOB_SETS)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from listings.ops import run_scheduled_jobs

        results = run_scheduled_jobs(jobs)
        ok = all(result["ok"] for result in results.values())
        return Response({"ok": ok, "jobs": jobs, "results": results})


def _swagger_ui_csp() -> str:
    """Content-Security-Policy for the API docs page only.

    Swagger UI's bundle, stylesheet and favicon come from the CDN configured in
    SPECTACULAR_SETTINGS["SWAGGER_UI_DIST"], which the site-wide policy
    (script-src 'self') rightly blocks everywhere else. The init script is
    served same-origin by the split view, so no inline script is allowed here
    either. Swagger UI injects inline styles, hence 'unsafe-inline' for styles.
    """
    cdn_origins: list[str] = []
    for resource in (spectacular_settings.SWAGGER_UI_DIST, spectacular_settings.SWAGGER_UI_FAVICON_HREF):
        parts = urlsplit(str(resource or ""))
        if parts.scheme in ("http", "https") and parts.netloc:
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin not in cdn_origins:
                cdn_origins.append(origin)
    cdn = "".join(f" {origin}" for origin in cdn_origins)
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self'{cdn}",
            f"style-src 'self' 'unsafe-inline'{cdn}",
            f"img-src 'self' data:{cdn}",
            "connect-src 'self'",
            "font-src 'self' data:",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )


@extend_schema(exclude=True)
class ApiDocsView(SpectacularSwaggerSplitView):
    """GET /api/docs/: Swagger UI with a Content-Security-Policy it can run under.

    The split view serves the page and its init script as two same-origin
    requests instead of one page with an inline script. The CSP middleware
    leaves a policy set by a view untouched, so this is the only route with
    the wider policy.
    """

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        response = super().get(request, *args, **kwargs)
        response["Content-Security-Policy"] = _swagger_ui_csp()
        return response


class FrontendAppView(View):
    """Serve the built SPA's index.html for client-side routes.

    Only mounted when settings.SERVE_FRONTEND (frontend/dist exists). HTML is
    served no-cache so every deploy's new asset hashes take effect immediately;
    the content-hashed files under /assets/ are served by WhiteNoise as
    immutable (see WHITENOISE_IMMUTABLE_FILE_TEST in settings).
    """

    def get(self, request: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        from django.conf import settings

        index = settings.FRONTEND_DIST / "index.html"
        response = HttpResponse(index.read_bytes(), content_type="text/html; charset=utf-8")
        response["Cache-Control"] = "no-cache"
        return response
