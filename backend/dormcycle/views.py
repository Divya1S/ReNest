from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpResponse
from django.views import View
from django.db import connections
from django.db.utils import OperationalError
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView


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
        except OperationalError as exc:
            ready = False
            warnings.append(f"Database check failed: {exc}")

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
        from dormcycle.celery import _DLQ_KEY

        # Eager mode: no Redis, no DLQ
        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            return Response({"mode": "eager", "length": 0})

        try:
            from django.core.cache import cache
            client = cache.client.get_client()
            length = client.llen(_DLQ_KEY)
            return Response({"length": length, "key": _DLQ_KEY})
        except Exception as exc:
            return Response(
                {"status": "error", "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


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
