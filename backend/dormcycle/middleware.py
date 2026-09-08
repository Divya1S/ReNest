import logging
import os
import threading
import uuid

_local = threading.local()


def get_request_id() -> str:
    return getattr(_local, "request_id", "-")


class RequestIdMiddleware:
    """
    Attach a request-id to every request so all log lines for a single
    request share a traceable identifier.

    Reads X-Request-Id from the incoming headers (useful when a load
    balancer sets it); otherwise generates a new UUID4.  The id is echoed
    back in the X-Request-Id response header.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        _local.request_id = request_id
        request.request_id = request_id
        try:
            response = self.get_response(request)
        finally:
            _local.request_id = "-"
        response["X-Request-Id"] = request_id
        return response


class RequestIdFilter(logging.Filter):
    """Inject the current request-id into every log record."""

    def filter(self, record):
        record.request_id = get_request_id()
        return True


class ContentSecurityPolicyMiddleware:
    """
    Emit a Content-Security-Policy header on every response.

    Policy rationale:
    - default-src 'self'          → block everything not explicitly allowed
    - script-src 'self'           → no inline scripts, no eval; Vite bundles are same-origin
    - style-src 'self' 'unsafe-inline'  → Tailwind purged CSS is injected as <style> by Vite in dev
    - img-src 'self' data: blob:  → listing images from local Django media + S3 CDN (added via env)
    - connect-src 'self'          → fetch/XHR/EventSource/WebSocket to same origin only
    - frame-ancestors 'none'      → belt-and-suspenders alongside X-Frame-Options: DENY
    - base-uri 'self'             → prevent base tag injection
    - form-action 'self'          → prevent form hijacking

    settings.MEDIA_CDN_BASE_URL (derived from AWS_S3_BUCKET_NAME and friends) is
    added to img-src when object storage is configured, so listing and room-scan
    photos load. Set DJANGO_CSP_REPORT_URI to enable violation reporting.
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
        connect_src = "'self'"
        # The Sentry browser SDK posts to its ingest host; without this the
        # frontend can never report an error from production.
        sentry_dsn = os.getenv("VITE_SENTRY_DSN", "") or os.getenv("SENTRY_FRONTEND_DSN", "")
        if sentry_dsn:
            from urllib.parse import urlsplit

            host = urlsplit(sentry_dsn).hostname
            if host:
                connect_src += f" https://{host}"
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
        # Don't overwrite a policy already set by a view (e.g. Swagger UI needs looser rules)
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self._policy
        return response
