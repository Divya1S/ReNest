import logging
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

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
    def test_smtp_without_password_raises_error(self):
        ids = _run()
        self.assertIn("dormcycle.E001", ids)

    @override_settings(DEBUG=False, EMAIL_BACKEND=_SMTP, EMAIL_HOST_PASSWORD="s3cr3t!")
    def test_smtp_with_password_passes(self):
        ids = _run()
        self.assertNotIn("dormcycle.E001", ids)

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        EMAIL_HOST_PASSWORD="",
    )
    def test_console_backend_without_password_passes(self):
        ids = _run()
        self.assertNotIn("dormcycle.E001", ids)

    @override_settings(DEBUG=True, EMAIL_BACKEND=_SMTP, EMAIL_HOST_PASSWORD="")
    def test_smtp_without_password_in_debug_is_ignored(self):
        ids = _run()
        self.assertNotIn("dormcycle.E001", ids)


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
