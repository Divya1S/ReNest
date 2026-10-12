import logging
import os
import re
import uuid
from contextvars import ContextVar
from typing import Any

from asgiref.sync import iscoroutinefunction, markcoroutinefunction

_NO_REQUEST_ID = "-"

# A ContextVar rather than threading.local: an async view runs on the event
# loop thread, not the thread the sync middleware chain ran on, so a
# thread-local id is invisible to it. asgiref copies the context across its
# sync/async bridges, and every ASGI request gets its own task (own context).
_request_id_var: ContextVar[str] = ContextVar("request_id", default=_NO_REQUEST_ID)

# The id is echoed in a response header, written to every log line and stored
# in CharField(max_length=64) columns, so a caller-supplied value is only
# trusted when it is short and made of characters that are safe in all three.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str | None) -> None:
    """Bind a request id to the current execution context.

    Work that leaves the request's context (a streaming worker thread, an
    inline task) calls this so its log lines keep the id of the request that
    started it.
    """
    _request_id_var.set(request_id or _NO_REQUEST_ID)


def clean_request_id(candidate: str | None) -> str:
    """Return the caller-supplied id when it is well formed, else a new UUID4."""
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


class RequestIdMiddleware:
    """
    Attach a request-id to every request so all log lines for a single
    request share a traceable identifier.

    Reads X-Request-Id from the incoming headers (useful when a load
    balancer sets it) when it is 1 to 64 characters of [A-Za-z0-9._-];
    anything else is replaced with a new UUID4. The id is echoed back in the
    X-Request-Id response header.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self._is_async = iscoroutinefunction(get_response)
        if self._is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self._is_async:
            return self.__acall__(request)
        request_id = clean_request_id(request.headers.get("X-Request-Id"))
        request.request_id = request_id
        token = _request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id_var.reset(token)
        response["X-Request-Id"] = request_id
        return response

    async def __acall__(self, request: Any) -> Any:
        request_id = clean_request_id(request.headers.get("X-Request-Id"))
        request.request_id = request_id
        token = _request_id_var.set(request_id)
        try:
            response = await self.get_response(request)
        finally:
            _request_id_var.reset(token)
        response["X-Request-Id"] = request_id
        return response


class RequestIdFilter(logging.Filter):
    """Inject the current request-id into every log record."""

    def filter(self, record):
        request_id = get_request_id()
        if request_id == _NO_REQUEST_ID:
            # Django logs a 4xx/5xx response (django.request) after the whole
            # middleware chain has returned, when the context id is already
            # cleared. Those records carry the request itself, so read the id
            # from there. For django.server records "request" is a socket,
            # which simply has no request_id attribute.
            candidate = getattr(getattr(record, "request", None), "request_id", None)
            if isinstance(candidate, str) and candidate:
                request_id = candidate
        record.request_id = request_id
        return True


class ContentSecurityPolicyMiddleware:
    """
    Emit a Content-Security-Policy header on every response.

    Policy rationale:
    - default-src 'self'          → block everything not explicitly allowed
    - script-src 'self'           → no inline scripts, no eval; Vite bundles are same-origin
    - style-src 'self' 'unsafe-inline'  → Tailwind purged CSS is injected as <style> by Vite in dev
    - img-src 'self' data: blob:  → listing images from local Django media + S3 CDN (added via env)
    - connect-src 'self'          → fetch/XHR/EventSource/WebSocket to same origin, plus the
                                    object-storage origin that presigned photo uploads PUT to
    - frame-ancestors 'none'      → belt-and-suspenders alongside X-Frame-Options: DENY
    - base-uri 'self'             → prevent base tag injection
    - form-action 'self'          → prevent form hijacking

    settings.MEDIA_CDN_BASE_URL (derived from AWS_S3_BUCKET_NAME and friends) is
    added to img-src when object storage is configured, so listing and room-scan
    photos load. settings.MEDIA_UPLOAD_ORIGINS is added to connect-src for the
    same reason on the write path. Set DJANGO_CSP_REPORT_URI to enable
    violation reporting.
    """

    def __init__(self, get_response):
        from django.conf import settings

        self.get_response = get_response
        cdn = getattr(settings, "MEDIA_CDN_BASE_URL", "").rstrip("/")
        # OpenStreetMap tiles power the browse map view; the {s} placeholder
        # resolves to a/b/c subdomains, so allow the wildcard tile host.
        img_src = "'self' data: blob: https://*.tile.openstreetmap.org"
        if cdn:
            img_src += f" {cdn}"
        connect_sources = ["'self'"]
        # Listing photos are uploaded straight from the browser with a
        # presigned PUT (listings/views/uploads.py). That is a cross-origin
        # fetch to the bucket, which connect-src 'self' alone would block.
        for origin in getattr(settings, "MEDIA_UPLOAD_ORIGINS", []):
            if origin and origin not in connect_sources:
                connect_sources.append(origin)
        # The Sentry browser SDK posts to its ingest host; without this the
        # frontend can never report an error from production.
        sentry_dsn = os.getenv("VITE_SENTRY_DSN", "") or os.getenv("SENTRY_FRONTEND_DSN", "")
        if sentry_dsn:
            from urllib.parse import urlsplit

            host = urlsplit(sentry_dsn).hostname
            if host:
                connect_sources.append(f"https://{host}")
        connect_src = " ".join(connect_sources)
        report_uri = os.getenv("DJANGO_CSP_REPORT_URI", "")
        directives = [
            "default-src 'self'",
            # No 'unsafe-inline': the one script that must run before first
            # paint (the dark-mode bootstrap) is served from /theme-init.js.
            "script-src 'self'",
            # Google Fonts serves the Inter stylesheet + woff2 files
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            f"img-src {img_src}",
            f"connect-src {connect_src}",
            "font-src 'self' data: https://fonts.gstatic.com",
            "media-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        if report_uri:
            directives.append(f"report-uri {report_uri}")
        self._policy = "; ".join(directives)

    def __call__(self, request):
        response = self.get_response(request)
        # Don't overwrite a policy already set by a view (the API docs page
        # sets its own so Swagger UI can load from its CDN).
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self._policy
        return response
