from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User


class AuthApiTests(APITestCase):
    def test_register_me_and_logout_flow(self):
        payload = {
            "email": "jules@example.com",
            "display_name": "Jules",
            "campus_name": "Pacific State",
            "password": "moveoutmagic123",
            "confirm_password": "moveoutmagic123",
        }

        response = self.client.post(reverse("auth-register"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"]["email"], payload["email"])

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertTrue(me_response.data["authenticated"])

        logout_response = self.client.post(reverse("auth-logout"), {}, format="json")
        self.assertEqual(logout_response.status_code, status.HTTP_200_OK)

        me_after_logout = self.client.get(reverse("auth-me"))
        self.assertEqual(me_after_logout.status_code, status.HTTP_200_OK)
        self.assertFalse(me_after_logout.data["authenticated"])


class PasswordResetTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="reset@example.com",
            password="oldpassword123",
            display_name="Resetter",
            campus_name="Pacific State",
        )

    def _valid_uid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        return uid, token

    def test_request_with_known_email_returns_200(self):
        response = self.client.post(
            reverse("auth-password-reset"),
            {"email": "reset@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("reset link", response.data["detail"])

    def test_request_with_unknown_email_still_returns_200(self):
        # Don't reveal whether the address is registered
        response = self.client.post(
            reverse("auth-password-reset"),
            {"email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_confirm_with_valid_token_resets_password(self):
        uid, token = self._valid_uid_token()
        response = self.client.post(
            reverse("auth-password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "new_password": "newSecurePass99!",
                "confirm_password": "newSecurePass99!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newSecurePass99!"))

    def test_confirm_with_invalid_token_returns_400(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.post(
            reverse("auth-password-reset-confirm"),
            {
                "uid": uid,
                "token": "bad-token",
                "new_password": "newSecurePass99!",
                "confirm_password": "newSecurePass99!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_with_mismatched_passwords_returns_400(self):
        uid, token = self._valid_uid_token()
        response = self.client.post(
            reverse("auth-password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "new_password": "newSecurePass99!",
                "confirm_password": "differentPass99!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_cannot_be_reused_after_password_change(self):
        uid, token = self._valid_uid_token()
        # First use — succeeds
        self.client.post(
            reverse("auth-password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "new_password": "newSecurePass99!",
                "confirm_password": "newSecurePass99!",
            },
            format="json",
        )
        # Second use — token is now invalid (password hash changed)
        response = self.client.post(
            reverse("auth-password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "new_password": "anotherPass99!",
                "confirm_password": "anotherPass99!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class EmailVerificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="verify@example.com",
            password="testpass123",
            display_name="Verifier",
            campus_name="Pacific State",
        )
        self.client.force_authenticate(self.user)

    def _valid_token(self):
        return signing.dumps(self.user.pk, salt="email-verify")

    def test_send_verification_email_returns_200(self):
        response = self.client.post(reverse("auth-verify-email-send"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_send_returns_200_if_already_verified(self):
        self.user.email_verified = True
        self.user.save()
        response = self.client.post(reverse("auth-verify-email-send"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_send_includes_usable_dev_link_in_debug(self):
        with self.settings(DEBUG=True):
            response = self.client.post(reverse("auth-verify-email-send"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        dev_url = response.data["dev_verify_url"]
        self.assertIn("/verify-email/confirm?token=", dev_url)
        token = dev_url.split("token=", 1)[1]
        confirm = self.client.post(
            reverse("auth-verify-email-confirm"), {"token": token}, format="json"
        )
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_send_omits_dev_link_outside_debug(self):
        response = self.client.post(reverse("auth-verify-email-send"), {}, format="json")
        self.assertNotIn("dev_verify_url", response.data)

    def test_register_includes_dev_link_only_in_debug(self):
        payload = {
            "email": "fresh@example.com",
            "display_name": "Fresh",
            "campus_name": "Pacific State",
            "password": "moveoutmagic123",
            "confirm_password": "moveoutmagic123",
        }
        self.client.logout()
        with self.settings(DEBUG=True):
            response = self.client.post(reverse("auth-register"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("/verify-email/confirm?token=", response.data["dev_verify_url"])

        self.client.logout()
        from django.core.cache import cache

        cache.clear()  # reset the register throttle for the second attempt
        payload["email"] = "fresh2@example.com"
        response = self.client.post(reverse("auth-register"), payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("dev_verify_url", response.data)

    def test_confirm_with_valid_token_marks_verified(self):
        token = self._valid_token()
        response = self.client.post(
            reverse("auth-verify-email-confirm"),
            {"token": token},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_confirm_with_expired_token_returns_400(self):
        # Manually create a token that expired 25 hours ago
        token = signing.dumps(
            self.user.pk, salt="email-verify"
        )
        from unittest.mock import patch
        import time
        with patch("django.core.signing.time") as mock_time:
            mock_time.time.return_value = time.time() - 25 * 3600
            expired_token = signing.dumps(self.user.pk, salt="email-verify")
        response = self.client.post(
            reverse("auth-verify-email-confirm"),
            {"token": expired_token},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_with_bad_token_returns_400(self):
        response = self.client.post(
            reverse("auth-verify-email-confirm"),
            {"token": "not-a-real-token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unverified_user_cannot_create_listing(self):
        from listings.models import Listing
        from django.utils import timezone
        from datetime import timedelta
        from decimal import Decimal
        payload = {
            "title": "Test Lamp",
            "description": "A lamp.",
            "category": Listing.Category.LIGHTING,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.FREE,
            "price_amount": "0.00",
            "estimated_retail_value": "20.00",
            "pickup_zone": "North Hall",
            "available_until": (timezone.now() + timedelta(days=2)).isoformat(),
        }
        response = self.client.post("/api/listings", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_verified_user_can_create_listing(self):
        from listings.models import Listing
        from django.utils import timezone
        from datetime import timedelta
        self.user.email_verified = True
        self.user.save()
        payload = {
            "title": "Test Lamp",
            "description": "A lamp.",
            "category": Listing.Category.LIGHTING,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.FREE,
            "price_amount": "0.00",
            "estimated_retail_value": "20.00",
            "pickup_zone": "North Hall",
            "available_until": (timezone.now() + timedelta(days=2)).isoformat(),
        }
        response = self.client.post("/api/listings", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class CampusAutoMatchTests(APITestCase):
    """Registration assigns campus by exact email-domain match."""

    def setUp(self):
        from .models import Campus

        self.usc = Campus.objects.create(name="USC", slug="usc", email_domains="usc.edu, alumni.usc.edu")
        self.lookalike = Campus.objects.create(name="MyUSC College", slug="myusc", email_domains="myusc.edu")
        Campus.objects.create(name="Dormant U", slug="dormant", email_domains="dormant.edu", active=False)

    def _register(self, email):
        return self.client.post(
            "/api/auth/register",
            {
                "email": email,
                "display_name": "Test Student",
                "password": "pw-Str0ng!x88",
                "confirm_password": "pw-Str0ng!x88",
            },
            content_type="application/json",
        )

    def test_exact_domain_assigns_campus_and_name(self):
        from .models import User

        response = self._register("kid@usc.edu")
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="kid@usc.edu")
        self.assertEqual(user.campus, self.usc)
        self.assertEqual(user.campus_name, "USC")

    def test_secondary_domain_and_lookalike_isolation(self):
        from .models import Campus, User

        self._register("grad@alumni.usc.edu")
        self.assertEqual(User.objects.get(email="grad@alumni.usc.edu").campus, self.usc)

        # "myusc.edu" contains "usc.edu" — the old icontains matcher confused
        # these; exact matching must not.
        self._register("kid@myusc.edu")
        self.assertEqual(User.objects.get(email="kid@myusc.edu").campus, self.lookalike)
        self.assertIsNone(Campus.match_for_email("kid@notusc.edu"))

    def test_inactive_and_unknown_domains_leave_campus_null(self):
        from .models import User

        self._register("kid@dormant.edu")
        self.assertIsNone(User.objects.get(email="kid@dormant.edu").campus)
        self._register("kid@elsewhere.edu")
        self.assertIsNone(User.objects.get(email="kid@elsewhere.edu").campus)

    def test_backfill_command_assigns_existing_users(self):
        from io import StringIO

        from django.core.management import call_command

        from .models import User

        legacy = User.objects.create_user(email="old@usc.edu", password="pw-Str0ng!x88")
        outsider = User.objects.create_user(email="old@elsewhere.edu", password="pw-Str0ng!x88")
        out = StringIO()
        call_command("assign_campuses", stdout=out)
        legacy.refresh_from_db()
        outsider.refresh_from_db()
        self.assertEqual(legacy.campus, self.usc)
        self.assertIsNone(outsider.campus)
        self.assertIn("Assigned 1", out.getvalue())
