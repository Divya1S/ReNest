"""Regression tests for account and authentication defects.

Each class pins one defect that was found by audit and reproduced before it
was fixed. The class docstring says what used to go wrong.
"""

from __future__ import annotations

import os
from typing import Any, Callable
from unittest.mock import patch

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import serializers
from rest_framework.test import APIClient, APITestCase

from accounts import views as account_views
from accounts.models import Campus, User
from accounts.serializers import RegisterSerializer

PASSWORD = "pw-Str0ng!x88"


def _registration(email: str, **extra: str) -> dict[str, str]:
    payload = {
        "email": email,
        "display_name": "Test Student",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    payload.update(extra)
    return payload


class AnonymousEndpointTestCase(APITestCase):
    """Anonymous throttles count per IP in the cache; keep each test's budget its own."""

    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)


# --- P2: registering an email that differs only by case ---


class CaseInsensitiveEmailRegistrationTests(AnonymousEndpointTestCase):
    """A second sign-up differing only by letter case returned HTTP 500 (IntegrityError).

    The automatic UniqueValidator compared the raw input before validate_email
    lower-cased it, so the duplicate reached the database constraint.
    """

    TAKEN = {"email": ["user with this email already exists."]}

    def _register(self, email: str) -> Any:
        self.client.logout()
        return self.client.post(reverse("auth-register"), _registration(email), format="json")

    def test_upper_case_duplicate_of_a_lower_case_account(self) -> None:
        self.assertEqual(self._register("jules@example.com").status_code, 201)
        response = self._register("JULES@Example.COM")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, self.TAKEN)
        self.assertEqual(User.objects.filter(email__iexact="jules@example.com").count(), 1)

    def test_lower_case_duplicate_of_a_mixed_case_sign_up(self) -> None:
        self.assertEqual(self._register("Jules.Verne@Example.com").status_code, 201)
        # Stored canonically, so the first sign-up is findable by the lower-case form.
        self.assertTrue(User.objects.filter(email="jules.verne@example.com").exists())
        response = self._register("jules.verne@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, self.TAKEN)

    def test_mixed_case_row_also_blocks_the_lower_case_address(self) -> None:
        # A row written without the manager's normalisation (the admin can do this).
        User.objects.bulk_create([User(email="Legacy@Example.com", display_name="Legacy", password="!")])
        response = self._register("legacy@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, self.TAKEN)
        self.assertEqual(User.objects.filter(email__iexact="legacy@example.com").count(), 1)

    def test_exact_duplicate_keeps_the_same_error(self) -> None:
        self.assertEqual(self._register("same@example.com").status_code, 201)
        response = self._register("same@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, self.TAKEN)

    def test_sign_up_that_loses_a_race_gets_the_field_error(self) -> None:
        # Both requests pass validation; the other one commits first.
        serializer = RegisterSerializer(data=_registration("racer@example.com"))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        User.objects.create_user(email="racer@example.com", password=PASSWORD, display_name="First")
        with self.assertRaises(serializers.ValidationError) as caught:
            serializer.save()
        self.assertEqual(caught.exception.detail, self.TAKEN)
        # The failed insert did not poison the surrounding transaction.
        self.assertEqual(User.objects.filter(email="racer@example.com").count(), 1)


# --- P13: referral counter ---


class ReferralCounterTests(AnonymousEndpointTestCase):
    """referral_count was written back as "value read earlier + 1", losing concurrent sign-ups."""

    def setUp(self) -> None:
        super().setUp()
        self.referrer = User.objects.create_user(email="referrer@example.com", password=PASSWORD, display_name="Ref")

    def test_increment_is_applied_to_the_current_database_value(self) -> None:
        serializer = RegisterSerializer(
            data=_registration("friend@example.com", referral_code=self.referrer.referral_code)
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        # Other sign-ups with the same code commit between validation and save.
        User.objects.filter(pk=self.referrer.pk).update(referral_count=5)
        user = serializer.save()
        self.referrer.refresh_from_db()
        self.assertEqual(self.referrer.referral_count, 6)
        self.assertEqual(user.referred_by, self.referrer)

    def test_referral_through_the_endpoint_counts_once(self) -> None:
        response = self.client.post(
            reverse("auth-register"),
            _registration("friend2@example.com", referral_code=self.referrer.referral_code),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.referrer.refresh_from_db()
        self.assertEqual(self.referrer.referral_count, 1)


# --- P3: CSRF on the anonymous auth endpoints ---


class AnonymousAuthCsrfTests(AnonymousEndpointTestCase):
    """Login, register and password reset accepted cross-site POSTs (login CSRF).

    DRF only enforces CSRF for requests that already carry a session, and the
    form parsers let a plain HTML form on another site submit these endpoints.
    """

    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="csrf@example.com", password=PASSWORD, display_name="Csrf")
        self.client = APIClient(enforce_csrf_checks=True)

    def _token(self) -> str:
        """What the SPA does at boot: fetch the cookie, then echo it in a header."""
        response = self.client.get(reverse("auth-csrf"))
        self.assertEqual(response.status_code, 200)
        return self.client.cookies["csrftoken"].value

    def _reset_payload(self) -> dict[str, str]:
        # The token hashes last_login, which an earlier login in the test changed.
        self.user.refresh_from_db()
        return {
            "uid": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
            "new_password": "newSecurePass99!",
            "confirm_password": "newSecurePass99!",
        }

    def _cases(self) -> list[tuple[str, Callable[[], dict[str, str]], int]]:
        # Payloads are built when used, so the reset token is minted after the
        # login case has run.
        return [
            ("auth-login", lambda: {"email": "csrf@example.com", "password": PASSWORD}, 200),
            ("auth-register", lambda: _registration("newcomer@example.com"), 201),
            ("auth-password-reset", lambda: {"email": "csrf@example.com"}, 200),
            ("auth-password-reset-confirm", self._reset_payload, 200),
        ]

    def test_requests_without_a_token_are_rejected(self) -> None:
        for name, build_payload, _ in self._cases():
            with self.subTest(endpoint=name):
                response = self.client.post(reverse(name), build_payload(), format="json")
                self.assertEqual(response.status_code, 403)
                # Same error shape as every other API rejection.
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertTrue(response.json()["detail"].startswith("CSRF Failed"))
        # Nothing happened: no session, no account, password unchanged.
        self.assertNotIn("sessionid", self.client.cookies)
        self.assertFalse(User.objects.filter(email="newcomer@example.com").exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_cookie_alone_is_not_enough(self) -> None:
        self._token()
        response = self.client.post(
            reverse("auth-login"), {"email": "csrf@example.com", "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_requests_with_cookie_and_header_succeed(self) -> None:
        for name, build_payload, expected in self._cases():
            with self.subTest(endpoint=name):
                client = APIClient(enforce_csrf_checks=True)
                client.get(reverse("auth-csrf"))
                token = client.cookies["csrftoken"].value
                response = client.post(reverse(name), build_payload(), format="json", HTTP_X_CSRFTOKEN=token)
                self.assertEqual(response.status_code, expected, getattr(response, "data", None))
        # The reset really happened, through a CSRF-checked request.
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newSecurePass99!"))

    def test_login_with_token_establishes_a_session(self) -> None:
        token = self._token()
        response = self.client.post(
            reverse("auth-login"),
            {"email": "csrf@example.com", "password": PASSWORD},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get(reverse("auth-me")).data["authenticated"])

    def test_form_encoded_bodies_are_not_accepted(self) -> None:
        """Correct credentials and a valid token still do not log in from a form body."""
        token = self._token()
        urlencoded = self.client.post(
            reverse("auth-login"),
            "email=csrf%40example.com&password=" + PASSWORD,
            content_type="application/x-www-form-urlencoded",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(urlencoded.status_code, 415)
        multipart = self.client.post(
            reverse("auth-login"),
            {"email": "csrf@example.com", "password": PASSWORD},
            format="multipart",
            HTTP_X_CSRFTOKEN=token,
        )
        # No parser accepts it, so the fields never reach the serializer.
        self.assertIn(multipart.status_code, (400, 415))
        self.assertNotIn("sessionid", self.client.cookies)
        self.assertFalse(self.client.get(reverse("auth-me")).data["authenticated"])

    def test_all_four_views_are_json_only(self) -> None:
        from dormcycle.parsers import StrictJSONParser

        for view in (
            account_views.LoginView,
            account_views.RegisterView,
            account_views.PasswordResetRequestView,
            account_views.PasswordResetConfirmView,
        ):
            with self.subTest(view=view.__name__):
                self.assertEqual(view.parser_classes, [StrictJSONParser])

    def test_csrf_cookie_endpoint_itself_needs_no_token(self) -> None:
        self.assertEqual(self.client.get(reverse("auth-csrf")).status_code, 200)
        self.assertIn("csrftoken", self.client.cookies)


# --- P7: "rejectd" ---


class CampusDecisionWordingTests(APITestCase):
    """Rejecting a campus answered "Campus rejectd." and emailed "...has been rejectd"."""

    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            email="staff@example.com", password=PASSWORD, display_name="Staff", is_staff=True
        )
        self.client.force_authenticate(self.staff)

    def _campus(self, slug: str) -> Campus:
        return Campus.objects.create(
            name=f"{slug} University",
            slug=slug,
            email_domains=f"{slug}.edu",
            contact_name="Dana",
            contact_email=f"dana@{slug}.edu",
            active=False,
            onboarding_status=Campus.OnboardingStatus.PENDING,
        )

    def _decide(self, campus: Campus, action: str) -> Any:
        return self.client.patch(
            reverse("admin-campus-detail", kwargs={"pk": campus.pk}), {"action": action}, format="json"
        )

    def test_reject_is_spelled_correctly(self) -> None:
        campus = self._campus("northfield")
        response = self._decide(campus, "reject")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], "Campus rejected.")
        self.assertEqual(response.data["onboarding_status"], Campus.OnboardingStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Your ReNest campus request has been rejected")
        self.assertNotIn("rejectd", f"{mail.outbox[0].subject} {mail.outbox[0].body}")

    def test_approve_wording_is_unchanged(self) -> None:
        campus = self._campus("southfield")
        response = self._decide(campus, "approve")
        self.assertEqual(response.data["detail"], "Campus approved.")
        self.assertEqual(mail.outbox[0].subject, "Your ReNest campus request has been approved")
        campus.refresh_from_db()
        self.assertTrue(campus.active)


# --- P9: stale items in the accounts app ---


class PlatformAdminEmailTests(AnonymousEndpointTestCase):
    """PLATFORM_ADMIN_EMAIL was read with getattr but defined nowhere."""

    PAYLOAD = {
        "name": "Lakeside College",
        "slug": "lakeside",
        "email_domains": "lakeside.edu",
        "contact_name": "Sam",
        "contact_email": "sam@lakeside.edu",
    }

    @override_settings(PLATFORM_ADMIN_EMAIL="ops@renest.example")
    def test_onboarding_request_is_mailed_to_the_configured_inbox(self) -> None:
        response = self.client.post(reverse("campus-onboard"), self.PAYLOAD, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(mail.outbox[0].to, ["ops@renest.example"])
        self.assertIn("Lakeside College", mail.outbox[0].subject)

    def test_view_reads_the_setting_rather_than_a_silent_default(self) -> None:
        import inspect

        source = inspect.getsource(account_views.CampusOnboardingRequestView)
        self.assertIn("settings.PLATFORM_ADMIN_EMAIL", source)
        self.assertNotIn("getattr(settings", source)


class AccountsModuleHygieneTests(APITestCase):
    def test_unused_import_is_gone(self) -> None:
        self.assertFalse(hasattr(account_views, "import_module"))


class ImpactCardTests(APITestCase):
    """The card hard-coded a macOS font path; elsewhere every size fell back to a 10px bitmap face."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="impact@example.com", password=PASSWORD, display_name="Impact Maker", campus_name="Pacific State"
        )
        self.client.force_authenticate(self.user)

    def _no_system_fonts(self) -> Any:
        """Make every font file on disk unavailable, as on a bare Linux host.

        Pillow loads its own bundled font through the same function (from an
        in-memory file object), so only path lookups are failed.
        """
        from PIL import ImageFont

        real_truetype = ImageFont.truetype

        def only_bundled(font: Any = None, size: float = 10, *args: Any, **kwargs: Any) -> Any:
            if isinstance(font, (str, bytes, os.PathLike)):
                raise OSError("cannot open resource")
            return real_truetype(font, size, *args, **kwargs)

        return patch.object(ImageFont, "truetype", only_bundled)

    def test_download_is_named_for_renest(self) -> None:
        response = self.client.get(reverse("auth-impact-card"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn('filename="renest-impact.png"', response["Content-Disposition"])
        self.assertNotIn("dormcycle", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_fallback_fonts_are_scaled_to_the_requested_sizes(self) -> None:
        with self._no_system_fonts():
            fonts, full_unicode = account_views._load_card_fonts((72, 36, 28, 22))
        self.assertFalse(full_unicode)
        heights = [font.getbbox("Impact")[3] for font in fonts]
        # Largest first, and a headline that is actually headline sized.
        self.assertEqual(heights, sorted(heights, reverse=True))
        self.assertGreater(heights[0], 40)
        self.assertGreater(heights[0], heights[-1] * 2)

    def test_card_renders_without_any_system_font(self) -> None:
        import io

        from PIL import Image, ImageDraw

        drawn: list[str] = []
        original_text = ImageDraw.ImageDraw.text

        def recording_text(draw: Any, xy: Any, text: str, *args: Any, **kwargs: Any) -> Any:
            drawn.append(text)
            return original_text(draw, xy, text, *args, **kwargs)

        with self._no_system_fonts(), patch.object(ImageDraw.ImageDraw, "text", recording_text):
            png = account_views.ImpactCardView._render_card(
                name="Impact Maker", campus="Pacific State", item_count=3, total_value=120.0, co2_kg=4.5
            )
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, (1200, 630))
        self.assertTrue(drawn)
        # Pillow's bundled font only covers basic Latin: anything else would
        # render as an empty box, so the fallback text must be plain ASCII.
        for text in drawn:
            with self.subTest(text=text):
                self.assertTrue(text.isascii())


class AccountDeletionSessionTests(AnonymousEndpointTestCase):
    """The docstring claimed sessions were hard-deleted; pin what actually protects the user."""

    def test_other_devices_stop_authenticating_after_deletion(self) -> None:
        User.objects.create_user(email="leaver@example.com", password=PASSWORD, display_name="Leaver")
        phone, laptop = APIClient(), APIClient()
        for device in (phone, laptop):
            response = device.post(
                reverse("auth-login"), {"email": "leaver@example.com", "password": PASSWORD}, format="json"
            )
            self.assertEqual(response.status_code, 200)
        self.assertTrue(laptop.get(reverse("auth-me")).data["authenticated"])

        response = phone.delete(reverse("auth-account-delete"), {"password": PASSWORD}, format="json")
        self.assertEqual(response.status_code, 200)

        # The laptop still holds its cookie, but the session no longer logs anyone in.
        self.assertFalse(laptop.get(reverse("auth-me")).data["authenticated"])
        self.assertFalse(phone.get(reverse("auth-me")).data["authenticated"])

    def test_docstring_no_longer_claims_sessions_are_hard_deleted(self) -> None:
        doc = " ".join((account_views.AccountDeleteView.__doc__ or "").split())
        self.assertNotIn("hard-deletes sessions", doc)
        self.assertIn("only the session making this request is deleted", doc)


# --- P17: field errors from PATCH /api/auth/me ---


class MeUpdateErrorShapeTests(APITestCase):
    """PATCH /api/auth/me returned {field: "message"}; every other endpoint returns {field: ["message"]}."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(email="me@example.com", password=PASSWORD, display_name="Me")
        self.client.force_authenticate(self.user)

    def test_every_validation_error_is_a_list_of_messages(self) -> None:
        cases = [
            ({"display_name": "   "}, "display_name"),
            ({"display_name": "x" * 121}, "display_name"),
            ({"campus_name": "x" * 121}, "campus_name"),
            ({"show_on_leaderboard": "yes"}, "show_on_leaderboard"),
            ({"onboarding_step": "soon"}, "onboarding_step"),
            ({"onboarding_step": 9}, "onboarding_step"),
        ]
        for payload, field in cases:
            with self.subTest(payload=payload):
                response = self.client.patch(reverse("auth-me"), payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(response.data), [field])
                self.assertIsInstance(response.data[field], list)
                self.assertEqual(len(response.data[field]), 1)
                self.assertIsInstance(response.data[field][0], str)
                self.assertTrue(response.data[field][0])

    def test_shape_matches_a_serializer_validated_endpoint(self) -> None:
        me = self.client.patch(reverse("auth-me"), {"display_name": ""}, format="json")
        self.client.logout()
        register = self.client.post(reverse("auth-register"), {"email": "not-an-email"}, format="json")
        cache.clear()
        self.assertEqual(register.status_code, 400)
        self.assertIsInstance(register.data["email"], list)
        self.assertIs(type(me.data["display_name"]), type(register.data["email"]))

    def test_valid_update_still_works(self) -> None:
        response = self.client.patch(
            reverse("auth-me"), {"display_name": "  New Name  ", "show_on_leaderboard": False}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.display_name, "New Name")
        self.assertFalse(self.user.show_on_leaderboard)
