"""Regression tests for platform-layer defects (settings, middleware, Celery, ops scripts).

Each class pins one defect that was found by audit and reproduced before it
was fixed. The class docstring says what used to go wrong.
"""

from __future__ import annotations

import asyncio
import gzip
import importlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import path, reverse
from rest_framework.test import APIClient

from . import celery as celery_module
from .checks import dormcycle_runtime_checks
from .env import (
    DEFAULT_DEV_SECRET_KEY,
    KNOWN_DEV_SECRET_KEYS,
    is_default_secret_key,
    media_upload_origins,
    validate_environment,
)
from .middleware import (
    ContentSecurityPolicyMiddleware,
    RequestIdFilter,
    RequestIdMiddleware,
    get_request_id,
    set_request_id,
)

REPO_ROOT = Path(settings.BASE_DIR).parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


async def _echo_request_id(request):
    """An async view that reports the request id it can see."""
    return HttpResponse(get_request_id())


# URLconf for RequestIdAsgiStackTests (this module is its ROOT_URLCONF).
urlpatterns = [path("echo-request-id", _echo_request_id)]


def _csp_directive(policy: str, name: str) -> str:
    """The value of one directive from a Content-Security-Policy header."""
    for directive in policy.split(";"):
        directive = directive.strip()
        if directive.startswith(name + " ") or directive == name:
            return directive[len(name):].strip()
    return ""


# --- P1: Celery dead-letter handler and request id forwarding ---


class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple] = []

    def lpush(self, key, value):
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append(("lpush", key, value))

    def ltrim(self, key, start, end):
        self.calls.append(("ltrim", key, start, end))


class _FakeTask:
    name = "listings.send_reservation_confirmed_email"


class DeadLetterHandlerTests(SimpleTestCase):
    """The handler raised KeyError before the Redis push, so nothing was ever recorded.

    logger.error(..., extra={"args": ...}) collides with LogRecord.args. Celery
    swallows receiver exceptions, so the failure was invisible.
    """

    def _fire(self, client, **overrides):
        kwargs = dict(
            sender=_FakeTask(),
            task_id="abc-123",
            exception=ValueError("smtp refused"),
            args=(42,),
            kwargs={"reservation_pk": 42},
            traceback=None,
            einfo=None,
        )
        kwargs.update(overrides)
        with patch.object(celery_module, "redis_client", return_value=client):
            celery_module.on_task_failure(**kwargs)

    def test_pushes_and_trims_without_raising(self):
        client = _FakeRedis()
        with self.assertLogs("dormcycle.celery", level="ERROR") as logs:
            self._fire(client)

        self.assertEqual([call[0] for call in client.calls], ["lpush", "ltrim"])
        _, key, raw = client.calls[0]
        self.assertEqual(key, celery_module._DLQ_KEY)
        self.assertEqual(client.calls[1], ("ltrim", celery_module._DLQ_KEY, 0, celery_module._DLQ_MAX - 1))

        entry = json.loads(raw)
        self.assertEqual(entry["task"], _FakeTask.name)
        self.assertEqual(entry["task_id"], "abc-123")
        self.assertIn("smtp refused", entry["exc"])
        self.assertEqual(entry["args"], "(42,)")
        self.assertEqual(entry["kwargs"], "{'reservation_pk': 42}")
        self.assertTrue(entry["failed_at"])
        # The structured log line is still emitted, with non-reserved keys.
        record = logs.records[0]
        self.assertEqual(record.task_name, _FakeTask.name)
        self.assertEqual(record.task_args, "(42,)")

    def test_argument_summary_is_truncated(self):
        client = _FakeRedis()
        with self.assertLogs("dormcycle.celery", level="ERROR"):
            self._fire(client, args=("x" * 5000,), kwargs={"blob": "y" * 5000})
        entry = json.loads(client.calls[0][2])
        self.assertLessEqual(len(entry["args"]), celery_module._DLQ_SUMMARY_MAX)
        self.assertLessEqual(len(entry["kwargs"]), celery_module._DLQ_SUMMARY_MAX)
        self.assertTrue(entry["args"].endswith("..."))

    def test_redis_outage_still_logs_and_does_not_raise(self):
        with self.assertLogs("dormcycle.celery", level="WARNING") as logs:
            self._fire(_FakeRedis(fail=True))
        levels = [record.levelname for record in logs.records]
        self.assertIn("WARNING", levels)  # the failed push
        self.assertIn("ERROR", levels)  # the failure itself is still reported

    def test_no_redis_configured_is_a_quiet_no_op(self):
        with self.assertLogs("dormcycle.celery", level="ERROR"):
            self._fire(None)

    def test_dispatch_through_the_real_celery_signal(self):
        """Celery turns a receiver exception into a return value; assert there is none."""
        from celery.signals import task_failure

        client = _FakeRedis()
        with patch.object(celery_module, "redis_client", return_value=client):
            with self.assertLogs("dormcycle.celery", level="ERROR"):
                responses = task_failure.send(
                    sender=_FakeTask(),
                    task_id="sig-1",
                    exception=RuntimeError("boom"),
                    args=[1, 2],
                    kwargs={},
                    traceback=None,
                    einfo=None,
                )
        ours = [response for receiver, response in responses if receiver is celery_module.on_task_failure]
        self.assertEqual(ours, [None])
        self.assertEqual(client.calls[0][0], "lpush")

    def test_a_real_failing_task_lands_in_the_dead_letter_list(self):
        """End to end: Celery runs a task that raises and calls the handler itself."""

        @celery_module.app.task(name="dormcycle.tests.always_fails")
        def always_fails(listing_pk, label="a"):
            raise ValueError("nope")

        self.addCleanup(celery_module.app.tasks.pop, "dormcycle.tests.always_fails", None)
        client = _FakeRedis()
        with patch.object(celery_module, "redis_client", return_value=client):
            # celery.app.trace logs the traceback too; captured to keep the output quiet.
            with self.assertLogs("dormcycle.celery", level="ERROR") as logs, self.assertLogs("celery.app.trace"):
                result = always_fails.apply(args=(7,), kwargs={"label": "b"}, throw=False)

        self.assertTrue(result.failed())
        self.assertEqual([call[0] for call in client.calls], ["lpush", "ltrim"])
        entry = json.loads(client.calls[0][2])
        self.assertEqual(entry["task"], "dormcycle.tests.always_fails")
        self.assertEqual(entry["task_id"], result.id)
        self.assertEqual(entry["args"], "(7,)")
        self.assertEqual(entry["kwargs"], "{'label': 'b'}")
        self.assertIn("ValueError", entry["exc"])
        self.assertIn("dormcycle.tests.always_fails", logs.output[0])
        self.assertIn(result.id, logs.output[0])


class TaskRequestIdTests(SimpleTestCase):
    """task_prerun reset the request id to "-" for any task without a request_id kwarg.

    In eager mode tasks run inline inside the web request, so every log line
    after the first inline task lost its request id.
    """

    def setUp(self):
        set_request_id(None)
        self.addCleanup(set_request_id, None)

    def test_task_without_request_id_keeps_the_active_one(self):
        set_request_id("req-web-1")
        celery_module.on_task_prerun(sender=None, task_id="t1", task=None, args=(), kwargs={"listing_pk": 5})
        self.assertEqual(get_request_id(), "req-web-1")
        celery_module.on_task_postrun(sender=None, task_id="t1")
        self.assertEqual(get_request_id(), "req-web-1")

    def test_task_with_request_id_binds_it_then_restores(self):
        set_request_id("req-web-1")
        celery_module.on_task_prerun(sender=None, task_id="t2", task=None, args=(), kwargs={"request_id": "req-task-9"})
        self.assertEqual(get_request_id(), "req-task-9")
        celery_module.on_task_postrun(sender=None, task_id="t2")
        self.assertEqual(get_request_id(), "req-web-1")
        self.assertNotIn("t2", celery_module._previous_request_ids)

    def test_worker_does_not_leak_an_id_into_the_next_task(self):
        celery_module.on_task_prerun(sender=None, task_id="t3", task=None, args=(), kwargs={"_request_id": "req-a"})
        self.assertEqual(get_request_id(), "req-a")
        celery_module.on_task_postrun(sender=None, task_id="t3")
        celery_module.on_task_prerun(sender=None, task_id="t4", task=None, args=(), kwargs={})
        self.assertEqual(get_request_id(), "-")

    def test_inline_task_sees_and_preserves_the_request_id(self):
        """End to end through Celery's own eager execution path."""

        @celery_module.app.task(name="dormcycle.tests.report_request_id")
        def report_request_id() -> str:
            return get_request_id()

        self.addCleanup(celery_module.app.tasks.pop, "dormcycle.tests.report_request_id", None)
        set_request_id("req-inline-7")
        result = report_request_id.apply()
        self.assertEqual(result.get(), "req-inline-7")
        self.assertEqual(get_request_id(), "req-inline-7")


# --- P4: maintenance token comparison ---


class MaintenanceTokenEncodingTests(TestCase):
    """A non-ASCII bearer token made hmac.compare_digest raise TypeError (HTTP 500)."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("internal-maintenance")

    @override_settings(MAINTENANCE_TOKEN="s3cret-token")
    def test_non_ascii_token_is_forbidden_not_a_server_error(self):
        for bearer in ("tökén", "токен", "s3cret-tokené"):
            with self.subTest(bearer=bearer):
                self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
                response = self.client.post(self.url, {"jobs": ["tick"]}, format="json")
                self.assertEqual(response.status_code, 403)

    @override_settings(MAINTENANCE_TOKEN="tökén")
    def test_non_ascii_token_still_matches_itself(self):
        # A server hands the header over as latin-1 text of the UTF-8 bytes.
        on_the_wire = "tökén".encode("utf-8").decode("latin-1")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {on_the_wire}")
        response = self.client.post(self.url, {"jobs": ["not-a-job-set"]}, format="json")
        # 400 (bad job set) proves the token was accepted without running any job.
        self.assertEqual(response.status_code, 400)

    @override_settings(MAINTENANCE_TOKEN="s3cret-token")
    def test_correct_ascii_token_is_still_accepted(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer s3cret-token")
        response = self.client.post(self.url, {"jobs": ["not-a-job-set"]}, format="json")
        self.assertEqual(response.status_code, 400)


# --- P5: logging configuration ---


class AppLoggerConfigTests(SimpleTestCase):
    """risk and concierge had no logger entry, so root's WARNING level dropped their INFO lines."""

    def test_every_first_party_app_has_a_configured_logger(self):
        configured = settings.LOGGING["loggers"]
        reference = configured["listings"]
        for app in ("dormcycle", "accounts", "listings", "hubs", "concierge", "risk"):
            with self.subTest(app=app):
                self.assertIn(app, configured)
                self.assertEqual(configured[app]["level"], reference["level"])
                self.assertEqual(configured[app]["handlers"], ["console"])

    def test_effective_level_matches_the_other_apps(self):
        expected = logging.getLogger("listings.views").getEffectiveLevel()
        for name in ("risk", "risk.middleware", "concierge", "concierge.engine"):
            with self.subTest(logger=name):
                self.assertEqual(logging.getLogger(name).getEffectiveLevel(), expected)

    def test_info_is_not_dropped_at_the_default_level(self):
        if settings.LOGGING["loggers"]["listings"]["level"] != "INFO":
            self.skipTest("DJANGO_LOG_LEVEL overrides the default level in this environment")
        self.assertTrue(logging.getLogger("risk.middleware").isEnabledFor(logging.INFO))
        self.assertTrue(logging.getLogger("concierge.engine").isEnabledFor(logging.INFO))


# --- P6: request id ---


class RequestIdValidationTests(SimpleTestCase):
    """Any X-Request-Id was accepted, then echoed, logged and stored in 64-char columns."""

    def setUp(self):
        self.factory = RequestFactory()

    def _resolve(self, header_value):
        seen = []
        middleware = RequestIdMiddleware(lambda request: seen.append(request.request_id) or HttpResponse())
        response = middleware(self.factory.get("/", HTTP_X_REQUEST_ID=header_value))
        self.assertEqual(response["X-Request-Id"], seen[0])
        return seen[0]

    def assertIsFreshUuid(self, value):
        self.assertEqual(str(uuid.UUID(value)), value)

    def test_well_formed_ids_are_kept(self):
        for value in ("a", "my-trace-id", "req_1.2-3", "A" * 64, "0123456789abcdefABCDEF._-"):
            with self.subTest(value=value):
                self.assertEqual(self._resolve(value), value)

    def test_overlong_id_is_replaced(self):
        self.assertIsFreshUuid(self._resolve("A" * 65))
        self.assertIsFreshUuid(self._resolve("x" * 5000))

    def test_ids_with_unsafe_characters_are_replaced(self):
        for value in (
            "has space",
            "semi;colon",
            "<script>alert(1)</script>",
            "quote\"inside",
            "tab\there",
            "new\nline",
            "café",
            "slash/path",
            "",
        ):
            with self.subTest(value=value):
                resolved = self._resolve(value)
                self.assertNotEqual(resolved, value)
                self.assertIsFreshUuid(resolved)

    def test_trailing_newline_is_not_accepted(self):
        # "$" in a regex would match before a trailing newline; fullmatch must not.
        self.assertIsFreshUuid(self._resolve("abc\n"))


class RequestIdContextTests(SimpleTestCase):
    """The id lived in threading.local, which an async view (another thread) cannot see."""

    def setUp(self):
        self.factory = RequestFactory()
        set_request_id(None)
        self.addCleanup(set_request_id, None)

    def test_async_view_behind_the_sync_chain_sees_the_id(self):
        async def view(request):
            return HttpResponse(get_request_id())

        # What Django does for an async view under a sync middleware chain.
        middleware = RequestIdMiddleware(async_to_sync(view))
        response = middleware(self.factory.get("/", HTTP_X_REQUEST_ID="trace-async-1"))
        self.assertEqual(response.content, b"trace-async-1")
        self.assertEqual(response["X-Request-Id"], "trace-async-1")
        self.assertEqual(get_request_id(), "-")

    def test_native_async_mode(self):
        async def view(request):
            await asyncio.sleep(0)
            return HttpResponse(get_request_id())

        middleware = RequestIdMiddleware(view)
        self.assertTrue(middleware._is_async)

        async def run():
            return await middleware(self.factory.get("/", HTTP_X_REQUEST_ID="trace-async-2"))

        response = asyncio.run(run())
        self.assertEqual(response.content, b"trace-async-2")
        self.assertEqual(response["X-Request-Id"], "trace-async-2")

    def test_concurrent_async_requests_do_not_share_an_id(self):
        async def view(request):
            # Yield so the two requests interleave on the one event loop thread.
            await asyncio.sleep(0.01)
            return HttpResponse(get_request_id())

        middleware = RequestIdMiddleware(view)

        async def run():
            return await asyncio.gather(
                middleware(self.factory.get("/", HTTP_X_REQUEST_ID="first")),
                middleware(self.factory.get("/", HTTP_X_REQUEST_ID="second")),
            )

        first, second = asyncio.run(run())
        self.assertEqual(first.content, b"first")
        self.assertEqual(second.content, b"second")

    def test_id_is_cleared_when_the_view_raises(self):
        def view(request):
            raise RuntimeError("boom")

        middleware = RequestIdMiddleware(view)
        with self.assertRaises(RuntimeError):
            middleware(self.factory.get("/", HTTP_X_REQUEST_ID="doomed"))
        self.assertEqual(get_request_id(), "-")

    def test_set_request_id_keeps_its_public_contract(self):
        set_request_id("explicit-1")
        self.assertEqual(get_request_id(), "explicit-1")
        set_request_id(None)
        self.assertEqual(get_request_id(), "-")
        set_request_id("")
        self.assertEqual(get_request_id(), "-")

    def test_a_new_thread_does_not_inherit_the_id(self):
        set_request_id("main-thread")
        seen = []
        worker = threading.Thread(target=lambda: seen.append(get_request_id()))
        worker.start()
        worker.join()
        self.assertEqual(seen, ["-"])


@override_settings(ROOT_URLCONF="dormcycle.test_platform_regressions")
class RequestIdAsgiStackTests(SimpleTestCase):
    """The deployed shape: uvicorn (ASGI), the project's real middleware list, an async view.

    The sync middlewares run in a worker thread and the view on the event
    loop, so only a context variable carries the id from one to the other.
    """

    async def test_async_view_sees_the_request_id_through_the_real_middleware(self):
        response = await self.async_client.get("/echo-request-id", headers={"X-Request-Id": "trace-asgi-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"trace-asgi-1")
        self.assertEqual(response["X-Request-Id"], "trace-asgi-1")

    async def test_generated_id_matches_the_response_header(self):
        response = await self.async_client.get("/echo-request-id")
        self.assertEqual(response.content.decode(), response["X-Request-Id"])
        self.assertEqual(str(uuid.UUID(response["X-Request-Id"])), response["X-Request-Id"])


class RequestIdLogFallbackTests(TestCase):
    """django.request logged every 4xx/5xx with rid=- (the id was cleared before Django logged)."""

    def _record(self, **attrs):
        record = logging.LogRecord("django.request", logging.WARNING, "", 0, "Not Found: /x", (), None)
        for key, value in attrs.items():
            setattr(record, key, value)
        return record

    def test_falls_back_to_the_request_carried_by_the_record(self):
        request = RequestFactory().get("/x")
        request.request_id = "from-request-1"
        record = self._record(request=request)
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, "from-request-1")

    def test_active_context_id_wins_over_the_record(self):
        request = RequestFactory().get("/x")
        request.request_id = "from-request-1"
        set_request_id("from-context-1")
        self.addCleanup(set_request_id, None)
        record = self._record(request=request)
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, "from-context-1")

    def test_record_without_a_usable_request_keeps_the_sentinel(self):
        # django.server passes a socket as "request"; some records pass None.
        for carried in (object(), None, RequestFactory().get("/no-middleware")):
            with self.subTest(carried=carried):
                record = self._record(request=carried)
                RequestIdFilter().filter(record)
                self.assertEqual(record.request_id, "-")

    def test_django_request_log_line_for_a_404_carries_the_response_id(self):
        with self.assertLogs("django.request", level="WARNING") as logs:
            response = self.client.get("/api/this-route-does-not-exist", HTTP_X_REQUEST_ID="trace-404")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["X-Request-Id"], "trace-404")
        record = logs.records[0]
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, "trace-404")


# --- P8: static caching and compression ---


class ImmutableAssetCachingTests(SimpleTestCase):
    """Vite's content-hashed bundles were served max-age=60, not immutable, and uncompressed."""

    def test_pattern_matches_vite_output_only(self):
        pattern = re.compile(settings.WHITENOISE_IMMUTABLE_FILE_TEST)
        hashed = [
            "/assets/index-AbC123xy.js",
            "/assets/index-AbC123xy.css",
            "/assets/BarChart-p0E-wlCG.js",  # the hash itself may contain "-"
            "/assets/AgentSandboxPage-BPk_bFAf.js",  # ... or "_"
            "/assets/index-AbC123xy.js.map",
            "/assets/inter-latin-400-Dx4kXJAl.woff2",
        ]
        unhashed = [
            "/index.html",
            "/sw.js",
            "/manifest.json",
            "/theme-init.js",
            "/images/og-image.png",
            "/assets/index.js",
            "/assets/vendor/index-AbC123xy.js",  # not something Vite emits
            "/static/admin/css/base.css",
            "/static/assets/index-AbC123xy.js",
        ]
        for url in hashed:
            with self.subTest(url=url):
                self.assertTrue(pattern.search(url))
        for url in unhashed:
            with self.subTest(url=url):
                self.assertFalse(pattern.search(url))

    def test_whitenoise_serves_hashed_assets_immutable_and_gzipped(self):
        from whitenoise.compress import Compressor
        from whitenoise.middleware import WhiteNoiseMiddleware

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            (dist / "assets").mkdir()
            bundle = dist / "assets" / "index-AbC123xy.js"
            bundle.write_text("console.log('renest');\n" * 400)
            (dist / "sw.js").write_text("self.addEventListener('fetch', () => {});\n" * 50)
            (dist / "index.html").write_text("<!doctype html><title>ReNest</title>")
            # Same step scripts/render-build.sh runs after `npm run build`.
            Compressor(use_brotli=False, quiet=True).compress(str(bundle))
            self.assertTrue((dist / "assets" / "index-AbC123xy.js.gz").is_file())

            with override_settings(WHITENOISE_ROOT=dist, DEBUG=False):
                middleware = WhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
            factory = RequestFactory()

            response = middleware(factory.get("/assets/index-AbC123xy.js", HTTP_ACCEPT_ENCODING="gzip, br"))
            self.assertEqual(response.status_code, 200)
            cache_control = response["Cache-Control"]
            self.assertIn("immutable", cache_control)
            self.assertIn("max-age=315360000", cache_control)
            self.assertEqual(response["Content-Encoding"], "gzip")
            self.assertEqual(gzip.decompress(b"".join(response.streaming_content)), bundle.read_bytes())
            response.close()

            # An unhashed file keeps the short lifetime: its URL is reused across deploys.
            response = middleware(factory.get("/sw.js"))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("immutable", response["Cache-Control"])
            self.assertIn("max-age=60", response["Cache-Control"])
            response.close()

    @skipUnless((SCRIPTS_DIR / "render-build.sh").is_file(), "scripts/ is not part of this checkout")
    def test_build_script_precompresses_the_spa(self):
        lines = [
            line.strip()
            for line in (SCRIPTS_DIR / "render-build.sh").read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        compress = [index for index, line in enumerate(lines) if "whitenoise.compress" in line and "frontend/dist" in line]
        build = [index for index, line in enumerate(lines) if line == "npm run build"]
        self.assertTrue(compress, "render-build.sh must run whitenoise.compress over frontend/dist")
        self.assertTrue(build)
        self.assertGreater(compress[0], build[0], "compression has to run after the SPA is built")


# --- P9: dead and stale settings ---


class StaleSettingsTests(SimpleTestCase):
    def test_removed_django_setting_is_gone(self):
        # SECURE_BROWSER_XSS_FILTER was removed from Django; setting it did nothing.
        self.assertFalse(hasattr(settings, "SECURE_BROWSER_XSS_FILTER"))

    def test_platform_admin_email_is_defined(self):
        self.assertTrue(hasattr(settings, "PLATFORM_ADMIN_EMAIL"))
        self.assertTrue(settings.PLATFORM_ADMIN_EMAIL)

    def test_platform_admin_email_comes_from_the_environment(self):
        import dormcycle.settings as settings_module

        with patch.dict(os.environ, {"PLATFORM_ADMIN_EMAIL": " ops@renest.example "}, clear=False), patch(
            "dotenv.load_dotenv", lambda *a, **k: False
        ):
            reloaded = importlib.reload(settings_module)
            try:
                self.assertEqual(reloaded.PLATFORM_ADMIN_EMAIL, "ops@renest.example")
            finally:
                importlib.reload(settings_module)

    def test_platform_admin_email_falls_back_to_the_sender(self):
        import dormcycle.settings as settings_module

        with patch.dict(os.environ, {"PLATFORM_ADMIN_EMAIL": ""}, clear=False), patch(
            "dotenv.load_dotenv", lambda *a, **k: False
        ):
            reloaded = importlib.reload(settings_module)
            try:
                self.assertEqual(reloaded.PLATFORM_ADMIN_EMAIL, reloaded.DEFAULT_FROM_EMAIL)
            finally:
                importlib.reload(settings_module)

    def test_schema_description_names_the_real_login_path(self):
        description = settings.SPECTACULAR_SETTINGS["DESCRIPTION"]
        self.assertNotIn("/api/auth/login/", description)
        self.assertIn(reverse("auth-login"), description)


class DocstringRouteTests(SimpleTestCase):
    """View docstrings advertised paths that do not exist (/api/campus/:slug/stats)."""

    _VERB = r"(?:GET|POST|PUT|PATCH|DELETE)"
    _ROUTE = re.compile(rf"\b{_VERB}(?:/{_VERB})*\s+(/api/\S+)")

    def _documented_paths(self, docstring):
        for raw in self._ROUTE.findall(docstring or ""):
            path = raw.split("?")[0].rstrip(".,:;)")
            path = re.sub(r"<slug>|:slug", "sample-slug", path)
            path = re.sub(r"<\w+>|:\w+", "1", path)
            yield path

    def test_documented_paths_are_real_routes(self):
        """Every "VERB /api/..." in a view docstring resolves; the first one is the view's own."""
        import inspect

        from django.urls import Resolver404, resolve
        from django.views import View

        import accounts.views
        import dormcycle.views
        import hubs.views

        checked = 0
        for module in (accounts.views, dormcycle.views, hubs.views):
            for _, view in inspect.getmembers(module, inspect.isclass):
                if view.__module__ != module.__name__ or not issubclass(view, View):
                    continue
                for index, path in enumerate(self._documented_paths(view.__doc__)):
                    with self.subTest(view=view.__name__, path=path):
                        try:
                            match = resolve(path)
                        except Resolver404:
                            self.fail(f"{view.__name__} documents {path}, which is not a route")
                        if index == 0:
                            # Later mentions may point at a related endpoint (login refers to /csrf).
                            self.assertIs(getattr(match.func, "view_class", None), view)
                        checked += 1
        # Guards the regex itself: if it stops matching, the test must not pass vacuously.
        self.assertGreaterEqual(checked, 12)


# --- P10: one scheduler definition ---


class BeatScheduleTests(SimpleTestCase):
    """Beat listed individual tasks and had drifted from listings.ops.SCHEDULED_JOB_SETS.

    It ran no risk jobs at all and used different cadences from the HTTP
    maintenance endpoint and `manage.py run_maintenance`.
    """

    def test_every_job_set_is_scheduled_exactly_once(self):
        from listings.ops import SCHEDULED_JOB_SETS

        scheduled = [
            entry["args"][0]
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if entry["task"] == celery_module.SCHEDULED_JOBS_TASK
        ]
        self.assertEqual(sorted(scheduled), sorted(SCHEDULED_JOB_SETS))

    def test_beat_schedules_nothing_but_job_sets(self):
        # An individual job listed next to its job set would run twice.
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            with self.subTest(entry=name):
                self.assertEqual(entry["task"], celery_module.SCHEDULED_JOBS_TASK)
                self.assertEqual(len(entry["args"]), 1)

    def test_scheduled_task_is_registered_with_celery(self):
        self.assertIn(celery_module.SCHEDULED_JOBS_TASK, celery_module.app.tasks)
        self.assertEqual(celery_module.run_scheduled_job_set.name, celery_module.SCHEDULED_JOBS_TASK)

    def test_task_runs_exactly_the_requested_job_set(self):
        calls = []

        def fake_run(job_sets):
            calls.append(list(job_sets))
            return {"expire": {"ok": True, "result": 3}, "digest": {"ok": True, "result": None}}

        with patch("listings.ops.run_scheduled_jobs", fake_run):
            with self.assertLogs("dormcycle.celery", level="INFO"):
                result = celery_module.run_scheduled_job_set("weekly")
        self.assertEqual(calls, [["weekly"]])
        self.assertEqual(result, {"job_set": "weekly", "ok": True, "jobs": ["digest", "expire"], "failed": []})
        json.dumps(result)  # the result backend serialises with JSON

    def test_task_reports_failed_jobs(self):
        def fake_run(job_sets):
            return {"good": {"ok": True, "result": 1}, "bad": {"ok": False, "error": "RuntimeError: kaboom"}}

        with patch("listings.ops.run_scheduled_jobs", fake_run):
            with self.assertLogs("dormcycle.celery", level="ERROR") as logs:
                result = celery_module.run_scheduled_job_set("tick")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], ["bad"])
        self.assertIn("bad", logs.output[0])

    def test_http_endpoint_accepts_the_same_job_sets(self):
        from listings.ops import SCHEDULED_JOB_SETS

        from .views import MAINTENANCE_JOB_SETS

        self.assertEqual(set(MAINTENANCE_JOB_SETS), set(SCHEDULED_JOB_SETS) | {"all"})


# --- P12: Content-Security-Policy ---


class UploadOriginCspTests(SimpleTestCase):
    """connect-src was 'self' (plus Sentry), so the browser blocked the presigned S3 PUT."""

    def _policy(self):
        middleware = ContentSecurityPolicyMiddleware(lambda request: HttpResponse())
        return middleware(RequestFactory().get("/"))["Content-Security-Policy"]

    @override_settings(MEDIA_UPLOAD_ORIGINS=[])
    def test_without_object_storage_connect_src_stays_same_origin(self):
        with patch.dict(os.environ, {"VITE_SENTRY_DSN": "", "SENTRY_FRONTEND_DSN": ""}):
            self.assertEqual(_csp_directive(self._policy(), "connect-src"), "'self'")

    @override_settings(MEDIA_UPLOAD_ORIGINS=["https://renest-media.s3.amazonaws.com", "https://renest-media.s3.us-west-2.amazonaws.com"])
    def test_bucket_origins_are_added_to_connect_src(self):
        connect_src = _csp_directive(self._policy(), "connect-src").split()
        self.assertEqual(connect_src[0], "'self'")
        self.assertIn("https://renest-media.s3.amazonaws.com", connect_src)
        self.assertIn("https://renest-media.s3.us-west-2.amazonaws.com", connect_src)

    @override_settings(MEDIA_UPLOAD_ORIGINS=["https://abc123.r2.cloudflarestorage.com"])
    def test_upload_origin_and_sentry_coexist(self):
        with patch.dict(os.environ, {"VITE_SENTRY_DSN": "https://key@o1.ingest.sentry.io/42"}):
            connect_src = _csp_directive(self._policy(), "connect-src").split()
        self.assertEqual(
            connect_src, ["'self'", "https://abc123.r2.cloudflarestorage.com", "https://o1.ingest.sentry.io"]
        )

    @override_settings(MEDIA_UPLOAD_ORIGINS=["https://abc123.r2.cloudflarestorage.com"])
    def test_script_src_is_not_widened(self):
        self.assertEqual(_csp_directive(self._policy(), "script-src"), "'self'")

    def test_origin_rules(self):
        self.assertEqual(media_upload_origins("", "us-east-1", ""), [])
        self.assertEqual(
            media_upload_origins("renest-media", "us-west-2", ""),
            ["https://renest-media.s3.amazonaws.com", "https://renest-media.s3.us-west-2.amazonaws.com"],
        )
        self.assertEqual(
            media_upload_origins("media.renest.app", "eu-central-1", ""),
            ["https://s3.amazonaws.com", "https://s3.eu-central-1.amazonaws.com"],
        )
        self.assertEqual(
            media_upload_origins("renest-media", "auto", "https://abc123.r2.cloudflarestorage.com/"),
            ["https://abc123.r2.cloudflarestorage.com"],
        )
        self.assertEqual(
            media_upload_origins("renest-media", "us-east-1", "http://localhost:9000"),
            ["http://localhost:9000"],
        )

    def test_rules_cover_the_urls_boto3_actually_signs(self):
        """The presigned URL built the way listings/views/uploads.py builds it must be allowed."""
        try:
            import boto3
        except ImportError:  # pragma: no cover - boto3 is a hard requirement
            self.skipTest("boto3 is not installed")
        from urllib.parse import urlsplit

        cases = [
            ("renest-media", "us-east-1", ""),
            ("renest-media", "us-west-2", ""),
            ("renest-media", "eu-central-1", ""),
            ("media.renest.app", "us-west-2", ""),
            ("renest-media", "auto", "https://abc123.r2.cloudflarestorage.com"),
            ("renest-media", "us-east-1", "http://localhost:9000"),
        ]
        for bucket, region, endpoint in cases:
            with self.subTest(bucket=bucket, region=region, endpoint=endpoint):
                client = boto3.client(
                    "s3",
                    region_name=region,
                    endpoint_url=endpoint or None,
                    aws_access_key_id="AKIAEXAMPLEEXAMPLE00",
                    aws_secret_access_key="example-secret",
                )
                url = client.generate_presigned_url(
                    "put_object",
                    Params={"Bucket": bucket, "Key": "listing-images/1/a.jpg", "ContentType": "image/jpeg", "ContentLength": 10},
                    ExpiresIn=300,
                )
                parts = urlsplit(url)
                self.assertIn(f"{parts.scheme}://{parts.netloc}", media_upload_origins(bucket, region, endpoint))

    def test_settings_derive_upload_origins_from_the_storage_environment(self):
        import dormcycle.settings as settings_module

        env = {
            "AWS_S3_BUCKET_NAME": "renest-media",
            "AWS_S3_REGION_NAME": "auto",
            "AWS_S3_ENDPOINT_URL": "https://abc123.r2.cloudflarestorage.com",
            "AWS_S3_CDN_DOMAIN": "media.renest.app",
        }
        with patch.dict(os.environ, env, clear=False), patch("dotenv.load_dotenv", lambda *a, **k: False):
            reloaded = importlib.reload(settings_module)
            try:
                # Reads go through the CDN domain, writes go to the storage endpoint.
                self.assertEqual(reloaded.MEDIA_CDN_BASE_URL, "https://media.renest.app")
                self.assertEqual(reloaded.MEDIA_UPLOAD_ORIGINS, ["https://abc123.r2.cloudflarestorage.com"])
            finally:
                importlib.reload(settings_module)


class ApiDocsCspTests(TestCase):
    """Swagger UI loads from a CDN and used an inline script; script-src 'self' blocked both."""

    def test_docs_page_gets_a_policy_swagger_ui_can_run_under(self):
        response = self.client.get(reverse("api-docs"))
        self.assertEqual(response.status_code, 200)
        policy = response["Content-Security-Policy"]
        script_src = _csp_directive(policy, "script-src").split()
        self.assertIn("'self'", script_src)
        self.assertIn("https://cdn.jsdelivr.net", script_src)
        # Wider than the site policy only by the CDN: still no inline script or eval.
        self.assertNotIn("'unsafe-inline'", script_src)
        self.assertNotIn("'unsafe-eval'", script_src)
        self.assertIn("https://cdn.jsdelivr.net", _csp_directive(policy, "style-src").split())
        self.assertIn("https://cdn.jsdelivr.net", _csp_directive(policy, "img-src").split())
        self.assertEqual(_csp_directive(policy, "frame-ancestors"), "'none'")
        self.assertEqual(_csp_directive(policy, "object-src"), "'none'")

    def test_docs_page_has_no_inline_script(self):
        html = self.client.get(reverse("api-docs")).content.decode()
        scripts = re.findall(r"<script\b[^>]*>", html)
        self.assertTrue(scripts)
        for tag in scripts:
            with self.subTest(tag=tag):
                self.assertIn("src=", tag)
        # Every script the page loads is covered by the policy's script-src.
        for src in re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", html):
            with self.subTest(src=src):
                self.assertTrue(src.startswith("https://cdn.jsdelivr.net/") or src.startswith("/api/docs/"))

    def test_init_script_is_served_same_origin(self):
        response = self.client.get(reverse("api-docs"), {"script": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        self.assertIn("SwaggerUIBundle", response.content.decode())

    def test_other_routes_keep_the_strict_policy(self):
        response = self.client.get(reverse("health-live"))
        policy = response["Content-Security-Policy"]
        self.assertEqual(_csp_directive(policy, "script-src"), "'self'")
        self.assertNotIn("cdn.jsdelivr.net", policy)


# --- P14: development secret keys ---


class DevelopmentSecretKeyGuardTests(SimpleTestCase):
    """Only the settings fallback key was refused; docker-compose ships a different one."""

    PROD = dict(
        debug=False,
        allowed_hosts=["renest.app"],
        cors_allowed_origins=["https://renest.app"],
        csrf_trusted_origins=["https://renest.app"],
        email_backend="django.core.mail.backends.smtp.EmailBackend",
        email_host="smtp.example.com",
        media_remote=True,
    )

    def _placeholders_shipped_in_the_repo(self):
        found = {}
        compose = REPO_ROOT / "docker-compose.yml"
        if compose.is_file():
            match = re.search(r"DJANGO_SECRET_KEY:\s*\$\{DJANGO_SECRET_KEY:-([^}]+)\}", compose.read_text())
            if match:
                found["docker-compose.yml"] = match.group(1).strip()
        example = Path(settings.BASE_DIR) / ".env.example"
        if example.is_file():
            match = re.search(r"^DJANGO_SECRET_KEY=(.+)$", example.read_text(), flags=re.MULTILINE)
            if match:
                found[".env.example"] = match.group(1).strip()
        return found

    def test_every_placeholder_in_the_repo_is_a_known_default(self):
        placeholders = self._placeholders_shipped_in_the_repo()
        if not placeholders:
            self.skipTest("docker-compose.yml and .env.example are not part of this checkout")
        for source, key in placeholders.items():
            with self.subTest(source=source):
                self.assertTrue(is_default_secret_key(key), f"{source} ships a key the production guard accepts")

    def test_boot_guard_refuses_every_known_development_key(self):
        keys = set(KNOWN_DEV_SECRET_KEYS) | {"django-insecure-freshly-generated-by-startproject", ""}
        self.assertIn("dev-only-insecure-key-change-in-production", keys)
        for key in keys:
            with self.subTest(key=key):
                with self.assertRaises(ImproperlyConfigured):
                    validate_environment(secret_key=key, **self.PROD)

    def test_boot_guard_accepts_a_real_key(self):
        validate_environment(secret_key="k3" + "x9Qf-" * 12, **self.PROD)

    def test_debug_mode_accepts_development_keys(self):
        validate_environment(
            debug=True,
            secret_key="dev-only-insecure-key-change-in-production",
            allowed_hosts=[],
            cors_allowed_origins=[],
            csrf_trusted_origins=[],
        )

    def test_deploy_check_flags_every_known_development_key(self):
        for key in KNOWN_DEV_SECRET_KEYS:
            with self.subTest(key=key):
                with override_settings(DEBUG=False, SECRET_KEY=key), patch.dict(os.environ, {"DJANGO_DEBUG": "0"}):
                    ids = [result.id for result in dormcycle_runtime_checks(None)]
                self.assertIn("dormcycle.E000", ids)

    def test_deploy_check_ignores_a_test_run_on_a_development_machine(self):
        # The test runner forces settings.DEBUG off; DJANGO_DEBUG=1 says this is not a deployment.
        with override_settings(DEBUG=False, SECRET_KEY=DEFAULT_DEV_SECRET_KEY), patch.dict(os.environ, {"DJANGO_DEBUG": "1"}):
            ids = [result.id for result in dormcycle_runtime_checks(None)]
        self.assertNotIn("dormcycle.E000", ids)


# --- P15: backup and restore scripts ---

_GOOD_DUMP = """--
-- PostgreSQL database dump
--

CREATE TABLE public.drill (id integer NOT NULL, note text);
COPY public.drill (id, note) FROM stdin;
1\tone
\\.

--
-- PostgreSQL database dump complete
--

"""


@skipUnless(
    shutil.which("bash") and shutil.which("gzip") and (SCRIPTS_DIR / "backup.sh").is_file(),
    "needs bash, gzip and the scripts/ directory",
)
class BackupRestoreScriptTests(SimpleTestCase):
    """backup.sh reported success for a failed dump; restore.sh carried on past SQL errors.

    pg_dump and psql are replaced by stubs on PATH, so no database is touched.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="renest-backup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.backups = self.tmp / "backups"
        self.backups.mkdir()
        self.psql_log = self.tmp / "psql.log"

    def _stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def _run(self, script, **env):
        full_env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "BACKUP_DIR": str(self.backups),
            "POSTGRES_HOST": "db.invalid",
            "POSTGRES_PASSWORD": "pw",
            "MIN_DUMP_BYTES": "1",
            "RESTORE_GRACE_SECONDS": "0",
            "PSQL_LOG": str(self.psql_log),
            "DUMP_SOURCE": str(self.tmp / "dump.sql"),
        }
        full_env.update(env)
        return subprocess.run(
            ["bash", str(SCRIPTS_DIR / script)],
            env=full_env,
            # A stub that reads stdin must never wait on the test runner's terminal.
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _dumps(self):
        return sorted(path.name for path in self.backups.iterdir())

    def _old_dump(self):
        old = self.backups / "dormcycle_2020-01-01_02-00.sql.gz"
        with gzip.open(old, "wt") as handle:
            handle.write(_GOOD_DUMP)
        month_ago = time.time() - 30 * 24 * 3600
        os.utime(old, (month_ago, month_ago))
        return old

    # backup.sh

    def test_backup_fails_and_keeps_nothing_when_pg_dump_fails(self):
        # Partial output then a non-zero exit: with plain `set -e` gzip's exit
        # status hid this and a truncated file was reported as a good backup.
        self._stub("pg_dump", 'printf -- "-- PostgreSQL database dump\\n"\nexit 1\n')
        old = self._old_dump()
        result = self._run("backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Done.", result.stdout)
        # No new dump, no leftover partial file, and retention did not run.
        self.assertEqual(self._dumps(), [old.name])

    def test_backup_rejects_a_dump_without_the_completion_marker(self):
        self._stub("pg_dump", 'printf -- "-- PostgreSQL database dump\\nCREATE TABLE t (id int);\\n"\n')
        old = self._old_dump()
        result = self._run("backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("truncated", result.stderr)
        self.assertEqual(self._dumps(), [old.name])

    def test_backup_rejects_an_implausibly_small_dump(self):
        (self.tmp / "dump.sql").write_text(_GOOD_DUMP)
        self._stub("pg_dump", 'cat "$DUMP_SOURCE"\n')
        result = self._run("backup.sh", MIN_DUMP_BYTES="1000000")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._dumps(), [])

    def test_backup_rejects_a_non_numeric_retention(self):
        self._stub("pg_dump", "exit 0\n")
        result = self._run("backup.sh", RETAIN_DAYS="seven")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RETAIN_DAYS", result.stderr)

    def test_good_backup_is_kept_and_old_dumps_are_pruned(self):
        (self.tmp / "dump.sql").write_text(_GOOD_DUMP)
        self._stub("pg_dump", 'cat "$DUMP_SOURCE"\n')
        old = self._old_dump()
        result = self._run("backup.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        dumps = self._dumps()
        self.assertEqual(len(dumps), 1)
        self.assertNotEqual(dumps[0], old.name)
        self.assertRegex(dumps[0], r"^dormcycle_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.sql\.gz$")
        with gzip.open(self.backups / dumps[0], "rt") as handle:
            self.assertEqual(handle.read(), _GOOD_DUMP)

    # restore.sh

    def _write_dump(self, text, name="dormcycle_test.sql.gz"):
        path = self.tmp / name
        with gzip.open(path, "wt") as handle:
            handle.write(text)
        return path

    def _psql_stub(self, exit_code=0):
        self._stub(
            "psql",
            'printf "%s\\n" "$@" > "$PSQL_LOG"\ncat > "$PSQL_LOG.stdin"\n' + f"exit {exit_code}\n",
        )

    def test_restore_stops_on_the_first_error_inside_one_transaction(self):
        self._psql_stub()
        dump = self._write_dump(_GOOD_DUMP)
        result = self._run("restore.sh", DUMP_FILE=str(dump))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Done.", result.stdout)
        args = self.psql_log.read_text().split("\n")
        self.assertIn("ON_ERROR_STOP=1", args)
        self.assertIn("--single-transaction", args)
        sql = Path(str(self.psql_log) + ".stdin").read_text()
        # The drop travels in the same transaction as the data that replaces it.
        self.assertTrue(sql.startswith("DROP SCHEMA public CASCADE;\nCREATE SCHEMA public;\n"))
        self.assertIn("COPY public.drill", sql)

    def test_restore_reports_failure_when_psql_fails(self):
        self._psql_stub(exit_code=3)
        dump = self._write_dump(_GOOD_DUMP)
        result = self._run("restore.sh", DUMP_FILE=str(dump))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Done.", result.stdout)
        self.assertIn("FAILED", result.stderr)

    def test_restore_refuses_a_truncated_dump_before_touching_the_database(self):
        self._psql_stub()
        dump = self._write_dump("-- PostgreSQL database dump\nCREATE TABLE t (id int);\n")
        result = self._run("restore.sh", DUMP_FILE=str(dump))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nothing was changed", result.stderr)
        self.assertFalse(self.psql_log.exists(), "psql must not run for a dump that failed validation")

    def test_restore_refuses_a_corrupt_archive_before_touching_the_database(self):
        self._psql_stub()
        dump = self.tmp / "dormcycle_corrupt.sql.gz"
        dump.write_bytes(b"this is not gzip data")
        result = self._run("restore.sh", DUMP_FILE=str(dump))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.psql_log.exists())

    def test_restore_requires_a_dump_file(self):
        self._psql_stub()
        result = self._run("restore.sh", DUMP_FILE="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DUMP_FILE is required", result.stderr)
        self.assertFalse(self.psql_log.exists())


# --- P16: worker health endpoint ---


class WorkerHealthErrorTests(TestCase):
    """The public worker health endpoint returned str(exc), which can hold the broker URL."""

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    def test_broker_error_text_is_logged_not_returned(self):
        secret = "redis://:hunter2@10.0.0.5:6379/0"

        def explode(*args, **kwargs):
            raise ConnectionError(f"Error 111 connecting to {secret}. Connection refused.")

        with patch("celery.current_app.control.inspect", explode):
            # django.request is captured only to keep the 503 out of the test output.
            with self.assertLogs("dormcycle.views", level="WARNING") as logs, self.assertLogs("django.request"):
                response = self.client.get(reverse("health-worker"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error", "detail": "Worker status is unavailable."})
        self.assertNotIn("hunter2", response.content.decode())
        self.assertNotIn("10.0.0.5", response.content.decode())
        # The operator can still see what happened, server-side.
        self.assertIn("hunter2", "\n".join(logs.output))


# --- P3 (platform half): CSRF failures answer in the API's error shape ---


class CsrfFailureViewTests(SimpleTestCase):
    def test_setting_points_at_the_json_aware_view(self):
        self.assertEqual(settings.CSRF_FAILURE_VIEW, "dormcycle.views.csrf_failure")

    def test_api_paths_get_json(self):
        from .views import csrf_failure

        response = csrf_failure(RequestFactory().post("/api/auth/login"), reason="CSRF token missing.")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(json.loads(response.content), {"detail": "CSRF Failed: CSRF token missing."})

    def test_other_paths_keep_the_django_page(self):
        from .views import csrf_failure

        response = csrf_failure(RequestFactory().post("/admin/login/"), reason="CSRF token missing.")
        self.assertEqual(response.status_code, 403)
        self.assertIn("text/html", response["Content-Type"])
