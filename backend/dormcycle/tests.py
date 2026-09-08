import logging
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .checks import dormcycle_runtime_checks
from .middleware import RequestIdFilter, RequestIdMiddleware, _local, get_request_id
from .env import DEFAULT_DEV_SECRET_KEY

_SMTP = "django.core.mail.backends.smtp.EmailBackend"


def _run(**overrides):
    """Run checks and return (errors, warnings) id lists."""
    results = dormcycle_runtime_checks(None)
    ids = [r.id for r in results]
    return ids


class SecretKeyCheckTests(SimpleTestCase):
    @override_settings(DEBUG=False, SECRET_KEY=DEFAULT_DEV_SECRET_KEY)
    def test_dev_key_in_production_raises_error(self):
        ids = _run()
        self.assertIn("dormcycle.E000", ids)

    @override_settings(DEBUG=False, SECRET_KEY="super-secure-random-key-xyz-987")
    def test_rotated_key_passes(self):
        ids = _run()
        self.assertNotIn("dormcycle.E000", ids)

    @override_settings(DEBUG=True, SECRET_KEY=DEFAULT_DEV_SECRET_KEY)
    def test_dev_key_in_debug_mode_is_ignored(self):
        ids = _run()
        self.assertNotIn("dormcycle.E000", ids)


class EmailCredentialCheckTests(SimpleTestCase):
    @override_settings(DEBUG=False, EMAIL_BACKEND=_SMTP, EMAIL_HOST_PASSWORD="")
    def test_smtp_without_password_warns(self):
        ids = _run()
        self.assertIn("dormcycle.W004", ids)

    @override_settings(DEBUG=False, EMAIL_BACKEND=_SMTP, EMAIL_HOST_PASSWORD="s3cr3t!")
    def test_smtp_with_password_passes(self):
        ids = _run()
        self.assertNotIn("dormcycle.W004", ids)

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        EMAIL_HOST_PASSWORD="",
    )
    def test_console_backend_without_password_passes(self):
        ids = _run()
        self.assertNotIn("dormcycle.W004", ids)

    @override_settings(DEBUG=True, EMAIL_BACKEND=_SMTP, EMAIL_HOST_PASSWORD="")
    def test_smtp_without_password_in_debug_is_ignored(self):
        ids = _run()
        self.assertNotIn("dormcycle.W004", ids)


class PostgresPasswordCheckTests(SimpleTestCase):
    @override_settings(DEBUG=False, USE_SQLITE=False)
    def test_default_postgres_password_warns(self):
        with patch.dict("os.environ", {"POSTGRES_PASSWORD": "postgres"}):
            ids = _run()
        self.assertIn("dormcycle.W003", ids)

    @override_settings(DEBUG=False, USE_SQLITE=False)
    def test_custom_postgres_password_passes(self):
        with patch.dict("os.environ", {"POSTGRES_PASSWORD": "str0ng-p4ssw0rd!"}):
            ids = _run()
        self.assertNotIn("dormcycle.W003", ids)

    @override_settings(DEBUG=True, USE_SQLITE=False)
    def test_default_postgres_password_in_debug_is_ignored(self):
        with patch.dict("os.environ", {"POSTGRES_PASSWORD": "postgres"}):
            ids = _run()
        self.assertNotIn("dormcycle.W003", ids)

    @override_settings(DEBUG=False, USE_SQLITE=True)
    def test_sqlite_mode_skips_postgres_check(self):
        with patch.dict("os.environ", {"POSTGRES_PASSWORD": "postgres"}):
            ids = _run()
        self.assertNotIn("dormcycle.W003", ids)


class RequestIdMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _middleware(self, handler):
        return RequestIdMiddleware(handler)

    def test_generates_request_id_when_header_absent(self):
        request = self.factory.get("/")
        captured = []
        self._middleware(lambda req: captured.append(req.request_id) or HttpResponse())(request)
        self.assertEqual(len(captured[0]), 36)  # UUID4

    def test_propagates_incoming_request_id(self):
        request = self.factory.get("/", HTTP_X_REQUEST_ID="my-trace-id")
        captured = []
        self._middleware(lambda req: captured.append(req.request_id) or HttpResponse())(request)
        self.assertEqual(captured[0], "my-trace-id")

    def test_echoes_request_id_in_response_header(self):
        request = self.factory.get("/", HTTP_X_REQUEST_ID="echo-me")
        response = self._middleware(lambda _: HttpResponse())(request)
        self.assertEqual(response["X-Request-Id"], "echo-me")

    def test_request_id_cleared_after_response(self):
        request = self.factory.get("/")
        self._middleware(lambda _: HttpResponse())(request)
        self.assertEqual(get_request_id(), "-")


class RequestIdFilterTests(SimpleTestCase):
    def test_filter_returns_sentinel_outside_request(self):
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        RequestIdFilter().filter(record)
        self.assertEqual(record.request_id, "-")

    def test_filter_reflects_active_request_id(self):
        _local.request_id = "active-123"
        try:
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            RequestIdFilter().filter(record)
            self.assertEqual(record.request_id, "active-123")
        finally:
            _local.request_id = "-"


class ProductionReadinessWarningTests(SimpleTestCase):
    """validate_environment should warn (not crash) on unshippable email/media."""

    PROD_KWARGS = dict(
        debug=False,
        secret_key="a-real-production-secret",
        allowed_hosts=["renest.app"],
        cors_allowed_origins=["https://renest.app"],
        csrf_trusted_origins=["https://renest.app"],
    )

    def test_warns_when_email_and_media_are_unconfigured(self):
        import warnings as warnings_module

        from .env import validate_environment

        with warnings_module.catch_warnings(record=True) as caught:
            warnings_module.simplefilter("always")
            validate_environment(**self.PROD_KWARGS, email_host="", media_remote=False)
        messages = [str(w.message) for w in caught]
        self.assertTrue(any("Production email is not configured" in m for m in messages))
        self.assertTrue(any("Media storage is the local filesystem" in m for m in messages))

    def test_silent_when_email_and_media_are_configured(self):
        import warnings as warnings_module

        from .env import validate_environment

        with warnings_module.catch_warnings(record=True) as caught:
            warnings_module.simplefilter("always")
            validate_environment(
                **self.PROD_KWARGS,
                email_backend="django.core.mail.backends.smtp.EmailBackend",
                email_host="smtp.resend.com",
                media_remote=True,
            )
        self.assertEqual([str(w.message) for w in caught], [])

    def test_debug_mode_never_warns(self):
        import warnings as warnings_module

        from .env import validate_environment

        with warnings_module.catch_warnings(record=True) as caught:
            warnings_module.simplefilter("always")
            validate_environment(
                debug=True, secret_key="", allowed_hosts=[], cors_allowed_origins=[],
                csrf_trusted_origins=[], email_host="", media_remote=False,
            )
        self.assertEqual(list(caught), [])


class MaintenanceEndpointTests(TestCase):
    """POST /api/internal/maintenance/ lets an external cron replace Celery beat."""

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("internal-maintenance")

    @override_settings(MAINTENANCE_TOKEN="")
    def test_disabled_without_token(self):
        response = self.client.post(self.url, {"jobs": ["tick"]}, format="json")
        self.assertEqual(response.status_code, 404)

    @override_settings(MAINTENANCE_TOKEN="s3cret-token")
    def test_rejects_missing_or_wrong_token(self):
        self.assertEqual(self.client.post(self.url, {"jobs": ["tick"]}, format="json").status_code, 403)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(self.client.post(self.url, {"jobs": ["tick"]}, format="json").status_code, 403)

    @override_settings(MAINTENANCE_TOKEN="s3cret-token")
    def test_validates_job_sets(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer s3cret-token")
        response = self.client.post(self.url, {"jobs": ["bogus"]}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url, {"jobs": []}, format="json")
        self.assertEqual(response.status_code, 400)

    @override_settings(MAINTENANCE_TOKEN="s3cret-token")
    def test_runs_requested_jobs_and_reports_per_job_status(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer s3cret-token")
        response = self.client.post(self.url, {"jobs": ["tick", "daily"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["ok"], response.data)
        results = response.data["results"]
        for name in ("maintenance_cycle", "sweep_stale_confirmations", "check_saved_searches",
                     "refresh_trending_cache", "flush_quiet_queue", "recompute_demand_forecast"):
            self.assertIn(name, results)
            self.assertTrue(results[name]["ok"], results[name])
        self.assertNotIn("weekly_campus_digest", results)

    @override_settings(MAINTENANCE_TOKEN="s3cret-token")
    def test_one_failing_job_does_not_block_the_rest(self):
        from listings import ops

        def boom():
            raise RuntimeError("kaboom")

        with patch.dict(ops.SCHEDULED_JOB_SETS, {"tick": [("boom", boom), ("maintenance_cycle", ops.run_maintenance_cycle)]}):
            self.client.credentials(HTTP_AUTHORIZATION="Bearer s3cret-token")
            response = self.client.post(self.url, {"jobs": "tick"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["ok"])
        self.assertFalse(response.data["results"]["boom"]["ok"])
        self.assertIn("kaboom", response.data["results"]["boom"]["error"])
        self.assertTrue(response.data["results"]["maintenance_cycle"]["ok"])

    def test_default_command_runs_the_tick_set(self):
        from io import StringIO

        from django.core.management import call_command

        output = StringIO()
        call_command("run_maintenance", stdout=output)
        self.assertIn("Maintenance complete", output.getvalue())
        self.assertIn("check_saved_searches", output.getvalue())


class StrictJsonParserTests(TestCase):
    """A JSON array body must be a 400, never an AttributeError 500."""

    def test_array_body_is_rejected_cleanly(self):
        response = APIClient().post(reverse("auth-login"), data="[1, 2]", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("JSON object", str(response.data))

    def test_health_dlq_reports_eager_mode_without_redis(self):
        from django.contrib.auth import get_user_model

        staff = get_user_model().objects.create_user(email="ops@example.com", password="pass12345!", is_staff=True)
        client = APIClient()
        client.force_authenticate(staff)
        response = client.get(reverse("health-dlq"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["length"], 0)


class RenderHostnameTests(SimpleTestCase):
    """RENDER_EXTERNAL_HOSTNAME is trusted automatically so a blueprint deploy boots."""

    def test_render_hostname_extends_hosts_and_origins(self):
        import importlib
        import os

        import dormcycle.settings as settings_module

        env = {
            "RENDER_EXTERNAL_HOSTNAME": "renest-abc.onrender.com",
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "not-the-default-key-for-tests",
            "DJANGO_ALLOWED_HOSTS": "",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "",
            "CORS_ALLOWED_ORIGINS": "",
            "APP_BASE_URL": "",
            "USE_SQLITE": "1",
        }
        # A developer's backend/.env must not leak into this assertion.
        with patch.dict(os.environ, env, clear=False), patch("dotenv.load_dotenv", lambda *a, **k: False):
            os.environ.pop("APP_BASE_URL", None)
            reloaded = importlib.reload(settings_module)
            try:
                self.assertIn("renest-abc.onrender.com", reloaded.ALLOWED_HOSTS)
                self.assertIn("https://renest-abc.onrender.com", reloaded.CSRF_TRUSTED_ORIGINS)
                self.assertIn("https://renest-abc.onrender.com", reloaded.CORS_ALLOWED_ORIGINS)
                self.assertEqual(reloaded.APP_BASE_URL, "https://renest-abc.onrender.com")
            finally:
                importlib.reload(settings_module)


class DatabaseUrlParsingTests(SimpleTestCase):
    def test_sslmode_and_encoded_password_are_honoured(self):
        import importlib
        import os

        import dormcycle.settings as settings_module

        env = {
            "DATABASE_URL": "postgresql://ren%40est:p%40ss%3Aword@db.example.com:5433/renest?sslmode=require&channel_binding=require",
            "USE_SQLITE": "0",
            # The test runner forces SQLite unless this is set; we only inspect
            # the parsed settings and never open a connection.
            "USE_POSTGRES_FOR_TESTS": "1",
        }
        with patch.dict(os.environ, env, clear=False), patch("dotenv.load_dotenv", lambda *a, **k: False):
            reloaded = importlib.reload(settings_module)
            try:
                db = reloaded.DATABASES["default"]
                self.assertEqual(db["USER"], "ren@est")
                self.assertEqual(db["PASSWORD"], "p@ss:word")
                self.assertEqual(db["HOST"], "db.example.com")
                self.assertEqual(db["PORT"], "5433")
                self.assertEqual(db["NAME"], "renest")
                self.assertEqual(db["OPTIONS"], {"sslmode": "require", "channel_binding": "require"})
            finally:
                importlib.reload(settings_module)


class BlankEnvironmentVariableTests(SimpleTestCase):
    """A declared-but-unfilled hosting variable must not break the boot."""

    def test_blank_numeric_values_fall_back_to_defaults(self):
        from .env import env_float, env_int

        with patch.dict("os.environ", {"X_INT": "", "X_FLOAT": "  "}, clear=False):
            self.assertEqual(env_int("X_INT", 25), 25)
            self.assertEqual(env_float("X_FLOAT", 0.1), 0.1)

    def test_unparseable_values_warn_and_fall_back(self):
        import warnings as warnings_module

        from .env import env_int

        with patch.dict("os.environ", {"X_INT": "not-a-number"}, clear=False):
            with warnings_module.catch_warnings(record=True) as caught:
                warnings_module.simplefilter("always")
                self.assertEqual(env_int("X_INT", 7), 7)
        self.assertTrue(any("not an integer" in str(w.message) for w in caught))

    def test_real_values_are_used(self):
        from .env import env_float, env_int

        with patch.dict("os.environ", {"X_INT": "587", "X_FLOAT": "0.25"}, clear=False):
            self.assertEqual(env_int("X_INT", 25), 587)
            self.assertEqual(env_float("X_FLOAT", 0.1), 0.25)
