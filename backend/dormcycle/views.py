import hmac
import logging
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views import View
from django.db import connections
from django.db.utils import OperationalError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


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
            return Response({"status": "ok", "workers": workers})
        except Exception as exc:
            return Response(
                {"status": "error", "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


@extend_schema(exclude=True)
class HealthDlqView(APIView):
    """
    Staff-only endpoint — returns the length of the Celery dead-letter queue
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
        if not provided or not hmac.compare_digest(provided, token):
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


class FrontendAppView(View):
    """Serve the built SPA's index.html for client-side routes.

    Only mounted when settings.SERVE_FRONTEND (frontend/dist exists). HTML is
    served no-cache so every deploy's new asset hashes take effect immediately;
    the hashed assets themselves are served (cacheable) by WhiteNoise.
    """

    def get(self, request: Any, *args: Any, **kwargs: Any) -> HttpResponse:
        from django.conf import settings

        index = settings.FRONTEND_DIST / "index.html"
        response = HttpResponse(index.read_bytes(), content_type="text/html; charset=utf-8")
        response["Cache-Control"] = "no-cache"
        return response
