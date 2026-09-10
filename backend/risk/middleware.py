"""Request-level observability for /api/risk/*.

Counts requests and server errors, records latency into the risk reservoir,
and writes one structured log line per request carrying the request id, so a
transaction can be traced from the HTTP log to its events and audit entries.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse

from .services import metrics

logger = logging.getLogger("risk.http")

PREFIX = "/api/risk/"


class RiskMetricsMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith(PREFIX):
            return self.get_response(request)

        started = time.perf_counter()
        try:
            response = self.get_response(request)
        except Exception:
            self._record(request, 500, started)
            raise
        self._record(request, response.status_code, started)
        return response

    @staticmethod
    def _record(request: HttpRequest, status: int, started: float) -> None:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        metrics.increment("requests_total")
        if status >= 500:
            metrics.increment("errors_total")
        metrics.observe("request_ms", elapsed_ms)
        user: Any = getattr(request, "user", None)
        logger.info(
            "risk request",
            extra={
                "http_method": request.method,
                "http_path": request.path,
                "http_status": status,
                "duration_ms": elapsed_ms,
                "user_id": getattr(user, "pk", None) if getattr(user, "is_authenticated", False) else None,
                "request_id": getattr(request, "request_id", ""),
            },
        )
