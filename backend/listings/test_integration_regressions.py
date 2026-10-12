"""Regressions for defects that span more than one area of the app.

These were found while integrating the per-area audit fixes: rules that one
module enforced and a neighbouring module quietly did not.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Campus

from .models import Listing, RescueRequest
from .views.partner import validate_webhook_url

User = get_user_model()


def make_user(email: str, **extra: Any) -> Any:
    extra.setdefault("display_name", email.split("@")[0])
    extra.setdefault("email_verified", True)
    return User.objects.create_user(email=email, password="pw-Str0ng!x", **extra)


def make_request(seeker: Any, title: str) -> RescueRequest:
    return RescueRequest.objects.create(
        seeker=seeker,
        title=title,
        description="Needed for the new term.",
        category=Listing.Category.STORAGE,
        pickup_zone="North quad",
        needed_by=timezone.now() + timedelta(days=3),
        budget_amount=Decimal("10.00"),
        urgency=RescueRequest.Urgency.SOON,
    )


class RequestBoardCampusScopeTests(APITestCase):
    """The request board follows the verified campus, not the profile's
    free-text campus name, which any user can edit."""

    def setUp(self) -> None:
        self.campus_a = Campus.objects.create(name="Campus A", slug="campus-a", email_domains="a.edu")
        self.campus_b = Campus.objects.create(name="Campus B", slug="campus-b", email_domains="b.edu")
        self.member_a = make_user("ana@a.edu", campus=self.campus_a, campus_name="Campus A")
        self.other_a = make_user("ari@a.edu", campus=self.campus_a, campus_name="Campus A")
        self.member_b = make_user("bo@b.edu", campus=self.campus_b, campus_name="Campus B")
        self.request_a = make_request(self.member_a, "Storage bins wanted")

    def titles_seen_by(self, user: Any) -> set[str]:
        self.client.force_authenticate(user)
        response = self.client.get("/api/requests")
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        return {row["title"] for row in rows}

    def test_same_campus_members_see_each_other(self) -> None:
        self.assertIn("Storage bins wanted", self.titles_seen_by(self.other_a))

    def test_renaming_a_profile_campus_does_not_open_another_board(self) -> None:
        self.assertNotIn("Storage bins wanted", self.titles_seen_by(self.member_b))
        self.member_b.campus_name = "Campus A"
        self.member_b.save(update_fields=["campus_name"])
        self.assertNotIn("Storage bins wanted", self.titles_seen_by(self.member_b))

    def test_an_unverified_account_cannot_claim_a_campus_by_name(self) -> None:
        drifter = make_user("drifter@example.com", campus_name="Campus A")
        self.assertNotIn("Storage bins wanted", self.titles_seen_by(drifter))

    def test_unverified_accounts_with_the_same_name_still_share_a_board(self) -> None:
        first = make_user("one@example.com", campus_name="Night School")
        second = make_user("two@example.com", campus_name="Night School")
        make_request(first, "Desk lamp wanted")
        self.assertIn("Desk lamp wanted", self.titles_seen_by(second))

    def test_a_user_always_sees_their_own_request(self) -> None:
        self.assertIn("Storage bins wanted", self.titles_seen_by(self.member_a))

    def test_match_center_is_scoped_the_same_way(self) -> None:
        Listing.objects.create(
            owner=self.member_b,
            title="Storage crate",
            description="Sturdy and clean.",
            category=Listing.Category.STORAGE,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="South hall",
            available_until=timezone.now() + timedelta(days=3),
        )
        self.member_b.campus_name = "Campus A"
        self.member_b.save(update_fields=["campus_name"])
        self.client.force_authenticate(self.member_b)
        response = self.client.get("/api/match-center")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Storage bins wanted", str(response.json()))


class SharedUrlValidatorTests(APITestCase):
    """Webhook registration and image fetching share one outbound-URL rule set."""

    def test_plain_http_is_refused(self) -> None:
        self.assertIsNotNone(validate_webhook_url("http://example.com/hook"))

    def test_loopback_and_private_literals_are_refused(self) -> None:
        for url in ("https://127.0.0.1/hook", "https://10.0.0.8/hook", "https://169.254.169.254/latest/meta-data"):
            with self.subTest(url=url):
                self.assertIsNotNone(validate_webhook_url(url))
