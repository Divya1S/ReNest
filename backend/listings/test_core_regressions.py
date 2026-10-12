"""Regression tests for the core marketplace defects found by the audit.

One test class per defect. Each test fails against the code as it was before
the fix and passes after it, on SQLite and on PostgreSQL. Tests that need a
real database feature (row locks, the GIN index) are skipped on SQLite.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.test import APIClient, APITestCase

from accounts.models import Campus
from hubs.models import DonationHub

from .models import (
    BlockedUser,
    Dispute,
    HandoffFeedback,
    Listing,
    ListingInteractionEvent,
    Notification,
    PartnerAPIKey,
    RescueRequest,
    Reservation,
    ReservationMessage,
    RoomScanImage,
    RoomScanItemDraft,
    RoomScanSession,
    StorageHold,
    WebhookDelivery,
    WebhookEndpoint,
)

User = get_user_model()

IS_POSTGRES = connection.vendor == "postgresql"


def make_user(email: str, **extra: Any) -> Any:
    extra.setdefault("display_name", email.split("@")[0])
    extra.setdefault("email_verified", True)
    return User.objects.create_user(email=email, password="pw-Str0ng!x", **extra)


def make_listing(owner: Any, **extra: Any) -> Listing:
    fields = {
        "title": "Desk lamp",
        "description": "Bright, clean and still works.",
        "category": Listing.Category.LIGHTING,
        "condition": Listing.Condition.GOOD,
        "price_type": Listing.PriceType.FREE,
        "pickup_zone": "Maple Hall lobby",
        "available_until": timezone.now() + timedelta(days=3),
    }
    fields.update(extra)
    return Listing.objects.create(owner=owner, **fields)


def make_reservation(listing: Listing, claimant: Any, **extra: Any) -> Reservation:
    """A requested claim, with the listing flipped the way the API does it."""
    reservation = Reservation.objects.create(
        listing=listing, claimant=claimant, pickup_time_window="Fri 3-5pm", **extra
    )
    Listing.all_objects.filter(pk=listing.pk).update(status=Listing.Status.RESERVED)
    listing.status = Listing.Status.RESERVED
    return reservation


def result_ids(response: Any) -> set[int]:
    data = response.data
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    return {row["id"] for row in rows}


class CoreTestCase(APITestCase):
    def setUp(self) -> None:
        # Throttle counters, PIN attempt counters and dashboard payloads live
        # in the cache and key on primary keys that SQLite reuses across tests.
        cache.clear()


# ---------------------------------------------------------------------------
# L1: a reservation must not cross a campus boundary
# ---------------------------------------------------------------------------

class CrossCampusReservationTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.campus_a = Campus.objects.create(name="Alder University", slug="alder")
        self.campus_b = Campus.objects.create(name="Birch College", slug="birch")
        self.owner = make_user("owner@alder.test", campus=self.campus_a)
        self.listing = make_listing(self.owner)

    def _claim(self, user: Any) -> Any:
        self.client.force_authenticate(user)
        return self.client.post(
            reverse("reservations-list"),
            {"listing": self.listing.pk, "pickup_time_window": "Fri 3-5pm"},
            format="json",
        )

    def test_user_from_another_campus_cannot_reserve_by_id(self) -> None:
        outsider = make_user("student@birch.test", campus=self.campus_b)
        response = self._claim(outsider)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("listing", response.data)
        self.assertFalse(Reservation.objects.filter(listing=self.listing).exists())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.AVAILABLE)

    def test_same_campus_user_can_reserve(self) -> None:
        neighbour = make_user("neighbour@alder.test", campus=self.campus_a)
        self.assertEqual(self._claim(neighbour).status_code, 201)

    def test_user_without_a_campus_is_not_restricted(self) -> None:
        unaffiliated = make_user("someone@example.test")
        self.assertEqual(self._claim(unaffiliated).status_code, 201)

    def test_staff_are_exempt(self) -> None:
        staff = make_user("staff@birch.test", campus=self.campus_b, is_staff=True)
        self.assertEqual(self._claim(staff).status_code, 201)

    def test_free_text_campus_name_does_not_grant_access(self) -> None:
        # campus_name is editable through PATCH /api/auth/me; only the FK counts.
        outsider = make_user(
            "sneaky@birch.test", campus=self.campus_b, campus_name="Alder University"
        )
        self.assertEqual(self._claim(outsider).status_code, 400)

    def test_detail_stays_public_but_does_not_offer_the_claim(self) -> None:
        outsider = make_user("reader@birch.test", campus=self.campus_b)
        self.client.force_authenticate(outsider)
        response = self.client.get(reverse("listings-detail", kwargs={"pk": self.listing.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["can_reserve"])


# ---------------------------------------------------------------------------
# L2: campus-manager analytics are scoped on the verified campus FK
# ---------------------------------------------------------------------------

class CampusAnalyticsScopeTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.campus_a = Campus.objects.create(name="Alder University", slug="alder")
        self.campus_b = Campus.objects.create(name="Birch College", slug="birch")
        owner_a = make_user("owner@alder.test", campus=self.campus_a, campus_name="Alder University")
        owner_b = make_user("owner@birch.test", campus=self.campus_b, campus_name="Birch College")
        for index in range(2):
            make_listing(owner_a, title=f"Alder item {index}")
        for index in range(3):
            make_listing(owner_b, title=f"Birch item {index}")
        self.manager = make_user(
            "manager@alder.test",
            campus=self.campus_a,
            campus_name="Alder University",
            is_campus_manager=True,
        )

    def _analytics(self, user: Any, **params: Any) -> Any:
        self.client.force_authenticate(user)
        return self.client.get(reverse("campus-analytics"), params)

    def test_manager_cannot_rescope_by_editing_campus_name(self) -> None:
        # What PATCH /api/auth/me lets any user do.
        self.manager.campus_name = "Birch College"
        self.manager.save(update_fields=["campus_name"])
        response = self._analytics(self.manager)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["campus"], "Alder University")
        self.assertEqual(response.data["summary"]["total_posted"], 2)

    def test_manager_cannot_rescope_with_the_query_parameter(self) -> None:
        response = self._analytics(self.manager, campus="birch")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["campus"], "Alder University")
        self.assertEqual(response.data["summary"]["total_posted"], 2)

    def test_manager_without_a_verified_campus_is_refused(self) -> None:
        unverified = make_user(
            "manager@nowhere.test", campus_name="Birch College", is_campus_manager=True
        )
        self.assertEqual(self._analytics(unverified).status_code, 403)

    def test_staff_may_choose_a_campus(self) -> None:
        staff = make_user("staff@renest.test", is_staff=True)
        response = self._analytics(staff, campus="birch")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["campus"], "Birch College")
        self.assertEqual(response.data["summary"]["total_posted"], 3)
        self.assertEqual(self._analytics(staff, campus="no-such-campus").status_code, 404)
        everything = self._analytics(staff)
        self.assertEqual(everything.data["campus"], "all")
        self.assertEqual(everything.data["summary"]["total_posted"], 5)

    def _export(self, user: Any, **params: Any) -> tuple[Any, str]:
        self.client.force_authenticate(user)
        response: Any = self.client.get(reverse("campus-analytics-export"), params)
        body = b"".join(response.streaming_content).decode() if response.status_code == 200 else ""
        return response, body

    def test_export_is_scoped_the_same_way(self) -> None:
        self.manager.campus_name = "Birch College"
        self.manager.save(update_fields=["campus_name"])
        response, body = self._export(self.manager, campus="birch")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alder item 0", body)
        self.assertNotIn("Birch item", body)

        unverified = make_user(
            "export@nowhere.test", campus_name="Birch College", is_campus_manager=True
        )
        response, _ = self._export(unverified)
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# L3: transitions are validated under the row lock
# ---------------------------------------------------------------------------

class ReservationTransitionLockTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner-lock@example.test")
        self.claimant = make_user("claimant-lock@example.test")
        self.listing = make_listing(self.owner)
        self.reservation = make_reservation(self.listing, self.claimant)

    def test_confirm_on_a_reservation_cancelled_meanwhile_is_a_conflict(self) -> None:
        from .services import cancel_reservation, confirm_reservation

        stale = Reservation.objects.get(pk=self.reservation.pk)
        cancel_reservation(Reservation.objects.get(pk=self.reservation.pk), actor=self.claimant)

        with self.assertRaises(APIException) as caught:
            confirm_reservation(stale)
        self.assertEqual(caught.exception.status_code, 409)

        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(self.reservation.handoff_pin, "")
        self.assertEqual(self.listing.status, Listing.Status.AVAILABLE)

    def test_patch_that_loses_the_race_answers_409(self) -> None:
        from .serializers import ReservationSerializer

        real_validate = ReservationSerializer.validate

        def validate_then_lose_the_race(serializer: Any, attrs: Any) -> Any:
            # The other request commits between this one's validation (on an
            # unlocked copy) and its transition.
            result = real_validate(serializer, attrs)
            Reservation.objects.filter(pk=serializer.instance.pk).update(
                status=Reservation.Status.CANCELLED
            )
            return result

        self.client.force_authenticate(self.owner)
        with mock.patch.object(ReservationSerializer, "validate", validate_then_lose_the_race):
            response = self.client.patch(
                reverse("reservations-detail", kwargs={"pk": self.reservation.pk}),
                {"status": "confirmed"},
                format="json",
            )
        self.assertEqual(response.status_code, 409, response.data)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)

    def test_second_cancel_is_a_conflict_and_does_not_strike_twice(self) -> None:
        from .services import cancel_reservation, confirm_reservation

        confirm_reservation(self.reservation, notify=False)
        first = Reservation.objects.get(pk=self.reservation.pk)
        second = Reservation.objects.get(pk=self.reservation.pk)
        cancel_reservation(first, actor=self.owner)
        with self.assertRaises(APIException):
            cancel_reservation(second, actor=self.owner)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.trust_strikes, 1)

    def _unresolved_with_new_claimant(self) -> Reservation:
        """The sweep gave up on the first handoff and someone else claimed."""
        Reservation.objects.filter(pk=self.reservation.pk).update(
            status=Reservation.Status.EXPIRED_UNRESOLVED, handoff_pin="135790"
        )
        newcomer = make_user("newcomer-lock@example.test")
        return make_reservation(self.listing, newcomer)

    def test_completing_fails_cleanly_when_someone_else_now_holds_the_listing(self) -> None:
        other = self._unresolved_with_new_claimant()
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("reservation-verify-pin", kwargs={"pk": self.reservation.pk}),
            {"pin": "135790"},
            format="json",
        )
        self.assertEqual(response.status_code, 409, response.data)
        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.EXPIRED_UNRESOLVED)
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)
        self.assertEqual(other.status, Reservation.Status.REQUESTED)

    def test_cancelling_an_unresolved_handoff_leaves_another_claim_alone(self) -> None:
        self._unresolved_with_new_claimant()
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            reverse("reservations-detail", kwargs={"pk": self.reservation.pk}),
            {"status": "cancelled"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.listing.refresh_from_db()
        # Still held by the newcomer; reopening it would show a listing nobody can claim.
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)

    def test_cancel_past_the_deadline_expires_with_a_repost_token(self) -> None:
        from .services import cancel_reservation

        Listing.objects.filter(pk=self.listing.pk).update(
            available_until=timezone.now() - timedelta(hours=1)
        )
        cancel_reservation(self.reservation, actor=self.claimant)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.EXPIRED)
        self.assertTrue(self.listing.repost_token)


@skipUnless(IS_POSTGRES, "row locks are only real on PostgreSQL")
class ReservationTransitionConcurrencyTests(TransactionTestCase):
    def test_racing_confirm_and_cancel_end_in_one_consistent_state(self) -> None:
        from .services import cancel_reservation, confirm_reservation

        cache.clear()
        owner = make_user("owner-race@example.test")
        claimant = make_user("claimant-race@example.test")
        listing = make_listing(owner)
        reservation = make_reservation(listing, claimant)

        start = threading.Barrier(2)
        unexpected: list[BaseException] = []

        def run(transition: Any) -> None:
            try:
                stale = Reservation.objects.get(pk=reservation.pk)
                start.wait(timeout=10)
                transition(stale)
            except APIException:
                pass  # the loser of the race: ReservationConflict
            except BaseException as exc:  # noqa: BLE001 - reported below
                unexpected.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=run, args=(lambda r: confirm_reservation(r, notify=False),)),
            threading.Thread(target=run, args=(lambda r: cancel_reservation(r, actor=claimant),)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(unexpected, [])
        reservation.refresh_from_db()
        listing.refresh_from_db()
        # Whichever ran first, the cancel wins or follows: never "confirmed"
        # on top of a cancel that already reopened the listing.
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(listing.status, Listing.Status.AVAILABLE)


# ---------------------------------------------------------------------------
# L4: the handoff PIN is the only way to complete
# ---------------------------------------------------------------------------

class _StaleReadCache:
    """The real cache, except every read returns the default.

    That is what N parallel requests see when they all read a counter before
    any of them has written it back.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def get(self, key: str, default: Any = None, **kwargs: Any) -> Any:
        return default

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class HandoffPinTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        from .services import confirm_reservation

        self.owner = make_user("owner-pin@example.test")
        self.claimant = make_user("claimant-pin@example.test")
        self.listing = make_listing(self.owner)
        self.reservation = confirm_reservation(
            make_reservation(self.listing, self.claimant), notify=False
        )
        self.pin = self.reservation.handoff_pin
        self.detail_url = reverse("reservations-detail", kwargs={"pk": self.reservation.pk})
        self.verify_url = reverse("reservation-verify-pin", kwargs={"pk": self.reservation.pk})

    def _wrong_pin(self) -> str:
        return "000000" if self.pin != "000000" else "111111"

    def test_owner_cannot_complete_with_a_status_patch(self) -> None:
        self.client.force_authenticate(self.owner)
        response = self.client.patch(self.detail_url, {"status": "completed"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)

    def test_allowed_actions_never_offer_completed(self) -> None:
        for state in (Reservation.Status.CONFIRMED, Reservation.Status.EXPIRED_UNRESOLVED):
            Reservation.objects.filter(pk=self.reservation.pk).update(status=state)
            for user in (self.owner, self.claimant):
                self.client.force_authenticate(user)
                actions = self.client.get(self.detail_url).data["allowed_actions"]
                self.assertEqual(actions, ["cancelled"], (state, user.email))

    def test_verify_pin_completes_and_clears_the_stored_pin(self) -> None:
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.verify_url, {"pin": self.pin}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.COMPLETED)
        self.assertEqual(self.reservation.handoff_pin, "")
        self.assertEqual(self.listing.status, Listing.Status.PICKED_UP)

    def test_verify_pin_also_works_for_an_unresolved_handoff(self) -> None:
        Reservation.objects.filter(pk=self.reservation.pk).update(
            status=Reservation.Status.EXPIRED_UNRESOLVED
        )
        Listing.objects.filter(pk=self.listing.pk).update(status=Listing.Status.AVAILABLE)
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.verify_url, {"pin": self.pin}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.COMPLETED)

    def test_cancelling_clears_the_stored_pin(self) -> None:
        self.client.force_authenticate(self.claimant)
        response = self.client.patch(self.detail_url, {"status": "cancelled"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.handoff_pin, "")

    def test_pin_is_shown_only_to_the_claimant_while_it_is_usable(self) -> None:
        self.client.force_authenticate(self.owner)
        self.assertIsNone(self.client.get(self.detail_url).data["handoff_pin_display"])

        self.client.force_authenticate(self.claimant)
        self.assertEqual(self.client.get(self.detail_url).data["handoff_pin_display"], self.pin)

        Reservation.objects.filter(pk=self.reservation.pk).update(
            status=Reservation.Status.EXPIRED_UNRESOLVED
        )
        self.assertEqual(self.client.get(self.detail_url).data["handoff_pin_display"], self.pin)

        # Even if a closed row still carried a PIN, it must not be shown.
        for closed in (Reservation.Status.COMPLETED, Reservation.Status.CANCELLED):
            Reservation.objects.filter(pk=self.reservation.pk).update(status=closed)
            self.assertIsNone(self.client.get(self.detail_url).data["handoff_pin_display"])

    def test_attempt_limit_holds_when_requests_read_a_stale_count(self) -> None:
        from .views import handoff

        self.client.force_authenticate(self.owner)
        with mock.patch.object(handoff, "cache", _StaleReadCache(handoff.cache)):
            statuses = [
                self.client.post(self.verify_url, {"pin": self._wrong_pin()}, format="json").status_code
                for _ in range(9)
            ]
        self.assertEqual(statuses[:8], [400] * 8)
        self.assertEqual(statuses[8], 429)

    def test_correct_pin_is_refused_once_the_attempts_are_used_up(self) -> None:
        self.client.force_authenticate(self.owner)
        for _ in range(8):
            self.client.post(self.verify_url, {"pin": self._wrong_pin()}, format="json")
        response = self.client.post(self.verify_url, {"pin": self.pin}, format="json")
        self.assertEqual(response.status_code, 429)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)

    def test_non_ascii_pin_is_a_wrong_pin_not_a_crash(self) -> None:
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.verify_url, {"pin": "１２３４５６"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_only_the_owner_can_verify(self) -> None:
        self.client.force_authenticate(self.claimant)
        response = self.client.post(self.verify_url, {"pin": self.pin}, format="json")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# L5: donating, deleting or reposting must not leave a live claim behind
# ---------------------------------------------------------------------------

class DanglingClaimTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        from .services import confirm_reservation

        self.owner = make_user("owner-claim@example.test")
        self.claimant = make_user("claimant-claim@example.test")
        self.listing = make_listing(self.owner)
        self.reservation = confirm_reservation(
            make_reservation(self.listing, self.claimant), notify=False
        )
        self.client.force_authenticate(self.owner)

    def _claimant_was_told(self) -> bool:
        return Notification.objects.filter(
            user=self.claimant, dedupe_key=f"cancelled:{self.reservation.pk}:{self.claimant.pk}"
        ).exists()

    def test_donating_cancels_the_claim_and_tells_the_claimant(self) -> None:
        with mock.patch("listings.receipts.generate_donation_receipt", return_value=None):
            response = self.client.post(
                reverse("listings-donate", kwargs={"pk": self.listing.pk}), {}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(self.listing.status, Listing.Status.DONATED)
        self.assertTrue(self._claimant_was_told())

    def test_donation_is_one_transaction(self) -> None:
        self.client.raise_request_exception = False
        with mock.patch(
            "listings.receipts.generate_donation_receipt", side_effect=RuntimeError("storage down")
        ):
            response = self.client.post(
                reverse("listings-donate", kwargs={"pk": self.listing.pk}), {}, format="json"
            )
        self.assertEqual(response.status_code, 500)
        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        # Nothing half-applied: not DONATED with a claim still attached.
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)

    def test_junk_hub_id_is_a_400(self) -> None:
        response = self.client.post(
            reverse("listings-donate", kwargs={"pk": self.listing.pk}),
            {"hub_id": "not-a-number"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_deleting_cancels_the_claim(self) -> None:
        response = self.client.delete(reverse("listings-detail", kwargs={"pk": self.listing.pk}))
        self.assertEqual(response.status_code, 204)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)
        self.assertIsNotNone(Listing.all_objects.get(pk=self.listing.pk).deleted_at)
        self.assertTrue(self._claimant_was_told())

    def test_reposting_an_expired_listing_releases_its_stale_claim(self) -> None:
        Listing.objects.filter(pk=self.listing.pk).update(
            available_until=timezone.now() - timedelta(hours=2)
        )
        self.listing.refresh_from_db()
        self.listing.refresh_status()
        self.assertEqual(self.listing.status, Listing.Status.EXPIRED)

        response = self.client.post(
            reverse("listings-repost", kwargs={"pk": self.listing.pk}),
            {"token": self.listing.repost_token},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.reservation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CANCELLED)
        self.assertEqual(self.listing.status, Listing.Status.AVAILABLE)

        # And the relisted item really can be claimed again.
        self.client.force_authenticate(make_user("next-claim@example.test"))
        again = self.client.post(
            reverse("reservations-list"),
            {"listing": self.listing.pk, "pickup_time_window": "Sat 10am"},
            format="json",
        )
        self.assertEqual(again.status_code, 201, again.data)


# ---------------------------------------------------------------------------
# L6: every expiry path mints the repost token
# ---------------------------------------------------------------------------

class ExpiryPathTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner-expiry@example.test")
        self.listing = make_listing(self.owner)

    def test_my_listings_view_expires_through_the_model(self) -> None:
        Listing.objects.filter(pk=self.listing.pk).update(
            available_until=timezone.now() - timedelta(hours=1)
        )
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("listings-list"), {"mine": "1"})
        self.assertEqual(response.status_code, 200)

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.EXPIRED)
        self.assertTrue(self.listing.repost_token)
        self.assertIsNotNone(self.listing.repost_token_expires_at)

        # The token is what makes the listing revivable.
        repost = self.client.post(
            reverse("listings-repost", kwargs={"pk": self.listing.pk}),
            {"token": self.listing.repost_token},
            format="json",
        )
        self.assertEqual(repost.status_code, 200, repost.data)

    def test_admin_expire_action_mints_the_repost_token(self) -> None:
        from .admin import expire_selected_listings

        expire_selected_listings(None, None, Listing.objects.filter(pk=self.listing.pk))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.EXPIRED)
        self.assertTrue(self.listing.repost_token)


# ---------------------------------------------------------------------------
# L7: the reservation_confirmed partner webhook fires, from one place
# ---------------------------------------------------------------------------

class ReservationConfirmedWebhookTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.campus = Campus.objects.create(name="Alder University", slug="alder")
        self.owner = make_user("owner-hook@alder.test", campus=self.campus)
        self.claimant = make_user("claimant-hook@alder.test", campus=self.campus)
        self.listing = make_listing(self.owner)
        self.reservation = make_reservation(self.listing, self.claimant)
        self.client.force_authenticate(self.owner)

    def _confirm_by_patch(self) -> Any:
        return self.client.patch(
            reverse("reservations-detail", kwargs={"pk": self.reservation.pk}),
            {"status": "confirmed"},
            format="json",
        )

    def test_patch_confirm_dispatches_with_the_owners_campus(self) -> None:
        # The webhook is sent once the confirmation has committed.
        with mock.patch(
            "listings.views.partner.dispatch_webhook_event", return_value=0
        ) as dispatch, self.captureOnCommitCallbacks(execute=True):
            response = self._confirm_by_patch()
        self.assertEqual(response.status_code, 200, response.data)
        dispatch.assert_called_once()
        event, payload, campus_name = dispatch.call_args.args
        self.assertEqual(event, "reservation_confirmed")
        self.assertEqual(payload["id"], self.reservation.pk)
        self.assertEqual(payload["listing_id"], self.listing.pk)
        self.assertEqual(campus_name, "Alder University")

    def test_batch_confirm_dispatches_too(self) -> None:
        with mock.patch(
            "listings.views.partner.dispatch_webhook_event", return_value=0
        ) as dispatch, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("logistics-batch-confirm"),
                {"reservation_ids": [self.reservation.pk]},
                format="json",
            )
        self.assertEqual(response.data["confirmed"], [self.reservation.pk])
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[0], "reservation_confirmed")

    def test_a_delivery_row_is_created_for_a_subscribed_endpoint(self) -> None:
        key, _raw = PartnerAPIKey.generate(
            campus=self.campus, partner_name="Housing portal", scopes=["webhook"]
        )
        WebhookEndpoint.objects.create(
            partner=key,
            url="https://hooks.partner.test/renest",
            secret="s" * 32,
            events=["reservation_confirmed"],
        )
        with mock.patch(
            "listings.tasks.deliver_webhook.apply_async"
        ) as enqueue, self.captureOnCommitCallbacks(execute=True):
            response = self._confirm_by_patch()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            WebhookDelivery.objects.filter(event_type="reservation_confirmed").count(), 1
        )
        enqueue.assert_called_once()

    def test_a_failing_dispatch_is_logged_and_does_not_fail_the_confirm(self) -> None:
        with mock.patch(
            "listings.views.partner.dispatch_webhook_event", side_effect=RuntimeError("boom")
        ), self.assertLogs("listings.services", level="ERROR") as logs, self.captureOnCommitCallbacks(
            execute=True
        ):
            response = self._confirm_by_patch()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("reservation_confirmed webhook dispatch failed", "\n".join(logs.output))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.CONFIRMED)


# ---------------------------------------------------------------------------
# L8: a storage hold really blocks reservations, one active hold per listing
# ---------------------------------------------------------------------------

class StorageHoldTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.hub = DonationHub.objects.create(
            name="Reuse closet",
            campus_name="Alder University",
            zone_label="Student union",
            description="Staffed donation point.",
            open_instructions="Weekdays 4pm to 8pm.",
        )
        self.staff = make_user("hub-staff@example.test", is_staff=True)
        self.owner = make_user("owner-hold@example.test")
        self.claimant = make_user("claimant-hold@example.test")
        self.listing = make_listing(self.owner)

    def _hold(self, **extra: Any) -> StorageHold:
        fields = {
            "listing": self.listing,
            "hub": self.hub,
            "held_by": self.staff,
            "expires_at": timezone.now() + timedelta(days=2),
        }
        fields.update(extra)
        return StorageHold.objects.create(**fields)

    def _claim(self) -> Any:
        self.client.force_authenticate(self.claimant)
        return self.client.post(
            reverse("reservations-list"),
            {"listing": self.listing.pk, "pickup_time_window": "Fri 3-5pm"},
            format="json",
        )

    def _place_hold(self, **body: Any) -> Any:
        self.client.force_authenticate(self.staff)
        payload = {"listing_id": self.listing.pk, "hub_id": self.hub.pk}
        payload.update(body)
        return self.client.post(reverse("logistics-holds"), payload, format="json")

    def test_listing_on_hold_cannot_be_reserved(self) -> None:
        self._hold()
        response = self._claim()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("hold", str(response.data["listing"]).lower())
        self.assertFalse(Reservation.objects.filter(listing=self.listing).exists())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.AVAILABLE)

    def test_hold_is_reflected_in_can_reserve(self) -> None:
        self._hold()
        self.client.force_authenticate(self.claimant)
        response = self.client.get(reverse("listings-detail", kwargs={"pk": self.listing.pk}))
        self.assertFalse(response.data["can_reserve"])

    def test_a_released_or_lapsed_hold_does_not_block(self) -> None:
        self._hold(status=StorageHold.Status.RELEASED)
        self.assertEqual(self._claim().status_code, 201)

    def test_a_hold_past_its_expiry_does_not_block(self) -> None:
        self._hold(expires_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual(self._claim().status_code, 201)

    def test_database_allows_only_one_active_hold_per_listing(self) -> None:
        self._hold()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._hold()
        # Closed holds do not count against the limit.
        self._hold(status=StorageHold.Status.COLLECTED)

    def test_second_hold_is_409_and_a_lapsed_hold_is_replaced(self) -> None:
        self.assertEqual(self._place_hold().status_code, 201)
        self.assertEqual(self._place_hold().status_code, 409)

        lapsed = StorageHold.objects.get(listing=self.listing, status=StorageHold.Status.ACTIVE)
        StorageHold.objects.filter(pk=lapsed.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self._place_hold().status_code, 201)
        lapsed.refresh_from_db()
        self.assertEqual(lapsed.status, StorageHold.Status.RELEASED)
        self.assertEqual(
            StorageHold.objects.filter(listing=self.listing, status=StorageHold.Status.ACTIVE).count(),
            1,
        )

    def test_note_is_cut_to_what_the_column_holds(self) -> None:
        response = self._place_hold(note="n" * 500)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(StorageHold.objects.get(pk=response.data["id"]).note), 300)

    def test_a_claimed_listing_cannot_be_put_on_hold(self) -> None:
        make_reservation(self.listing, self.claimant)
        self.assertEqual(self._place_hold().status_code, 409)
        self.assertFalse(StorageHold.objects.filter(listing=self.listing).exists())


# ---------------------------------------------------------------------------
# L9: list endpoints cost a fixed number of queries
# ---------------------------------------------------------------------------

class ListQueryCountTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.viewer = make_user("viewer-queries@example.test")
        self.sequence = 0

    def _listing_from_scan(self) -> Listing:
        """A listing published from a room scan, with its own owner."""
        self.sequence += 1
        owner = make_user(f"owner-queries-{self.sequence}@example.test")
        session = RoomScanSession.objects.create(
            owner=owner, name=f"Sweep {self.sequence}", room_label="North 204"
        )
        listing = make_listing(owner, title=f"Item {self.sequence}", source_scan_session=session)
        RoomScanItemDraft.objects.create(
            scan_session=session,
            title=listing.title,
            category=listing.category,
            condition=listing.condition,
            price_type=listing.price_type,
            linked_listing=listing,
        )
        return listing

    def _completed_handoff(self) -> Reservation:
        """A finished handoff with feedback from both sides and unread chat."""
        listing = self._listing_from_scan()
        Listing.objects.filter(pk=listing.pk).update(status=Listing.Status.PICKED_UP)
        reservation = Reservation.objects.create(
            listing=listing,
            claimant=self.viewer,
            pickup_time_window="Fri 3-5pm",
            status=Reservation.Status.COMPLETED,
        )
        HandoffFeedback.objects.create(
            reservation=reservation, reviewer=self.viewer, reviewee=listing.owner, rating=5
        )
        HandoffFeedback.objects.create(
            reservation=reservation, reviewer=listing.owner, reviewee=self.viewer, rating=4
        )
        for body in ("On my way", "Thanks again"):
            ReservationMessage.objects.create(reservation=reservation, sender=listing.owner, body=body)
        return reservation

    def _query_count(self, url: str, expected_rows: int) -> tuple[int, Any]:
        self.client.force_authenticate(self.viewer)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], expected_rows)
        return len(queries), response

    def test_browse_queries_do_not_grow_with_the_number_of_listings(self) -> None:
        url = reverse("listings-list")
        for _ in range(2):
            self._listing_from_scan()
        self._query_count(url, 2)  # warm-up
        with_two, response = self._query_count(url, 2)
        self.assertIsNotNone(response.data["results"][0]["source_scan_item_id"])

        for _ in range(4):
            self._listing_from_scan()
        with_six, _ = self._query_count(url, 6)
        self.assertEqual(with_six, with_two)

    def test_reservation_list_queries_do_not_grow_with_the_number_of_reservations(self) -> None:
        url = reverse("reservations-list")
        for _ in range(2):
            self._completed_handoff()
        self._query_count(url, 2)  # warm-up
        with_two, response = self._query_count(url, 2)

        row = response.data["results"][0]
        # The cheaper path still has to return the same information.
        self.assertEqual(row["unread_messages"], 2)
        self.assertEqual(len(row["feedback_entries"]), 2)
        self.assertEqual(row["my_feedback"]["rating"], 5)
        self.assertFalse(row["can_leave_feedback"])
        self.assertEqual(row["feedback_summary"], {"count": 2, "average_rating": 4.5})
        self.assertEqual(row["listing_detail"]["reservation_count"], 1)
        self.assertIsNotNone(row["listing_detail"]["source_scan_item_id"])
        # Detail-only fields are not computed per row of a list.
        self.assertIsNone(row["listing_detail"]["owner_trust_summary"])

        for _ in range(4):
            self._completed_handoff()
        with_six, _ = self._query_count(url, 6)
        self.assertEqual(with_six, with_two)

    def test_reservation_detail_still_carries_the_owner_trust_summary(self) -> None:
        reservation = self._completed_handoff()
        self.client.force_authenticate(self.viewer)
        response = self.client.get(reverse("reservations-detail", kwargs={"pk": reservation.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data["listing_detail"]["owner_trust_summary"])


# ---------------------------------------------------------------------------
# L10: browse shows exactly what marketplace_listing_queryset allows
# ---------------------------------------------------------------------------

class BrowseVisibilityParityTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.campus_a = Campus.objects.create(name="Alder University", slug="alder")
        self.campus_b = Campus.objects.create(name="Birch College", slug="birch")
        self.viewer = make_user("viewer@alder.test", campus=self.campus_a)
        self.good_owner = make_user("good@alder.test", campus=self.campus_a)

        self.visible = make_listing(self.good_owner, title="Visible")
        self.reserved = make_listing(self.good_owner, title="Reserved", status=Listing.Status.RESERVED)

        blocked_owner = make_user("blocked@alder.test", campus=self.campus_a)
        BlockedUser.objects.create(blocker=self.viewer, blocked=blocked_owner)
        self.blocked = make_listing(blocked_owner, title="From someone I blocked")

        blocking_owner = make_user("blocking@alder.test", campus=self.campus_a)
        BlockedUser.objects.create(blocker=blocking_owner, blocked=self.viewer)
        self.blocking = make_listing(blocking_owner, title="From someone who blocked me")

        self.demo = make_listing(self.good_owner, title="Demo", is_demo=True)

        other_campus_owner = make_user("owner@birch.test", campus=self.campus_b)
        self.other_campus = make_listing(other_campus_owner, title="Other campus")

        poor_owner = make_user("flaky@alder.test", campus=self.campus_a, completion_rate=0.1)
        self.poor = make_listing(poor_owner, title="Poor completion owner")

        self.flagged = make_listing(
            self.good_owner, title="Flagged", moderation_status=Listing.ModerationStatus.FLAGGED
        )
        self.expired = make_listing(self.good_owner, title="Expired", status=Listing.Status.EXPIRED)
        self.overdue = make_listing(
            self.good_owner, title="Overdue", available_until=timezone.now() - timedelta(hours=1)
        )
        self.picked_up = make_listing(
            self.good_owner, title="Picked up", status=Listing.Status.PICKED_UP
        )
        self.deleted = make_listing(self.good_owner, title="Deleted", deleted_at=timezone.now())

    def _browse(self, user: Any = None, **params: Any) -> set[int]:
        if user is None:
            self.client.force_authenticate(None)
        else:
            self.client.force_authenticate(user)
        response = self.client.get(reverse("listings-list"), {"page_size": 100, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return result_ids(response)

    def test_browse_and_marketplace_queryset_return_the_same_listings(self) -> None:
        from .views.helpers import marketplace_listing_queryset

        expected = {self.visible.pk, self.reserved.pk}
        shared = set(marketplace_listing_queryset(self.viewer).values_list("id", flat=True))
        self.assertEqual(shared, expected)
        self.assertEqual(self._browse(self.viewer), expected)

    def test_guest_browse_matches_the_guest_marketplace_queryset(self) -> None:
        from .views.helpers import marketplace_listing_queryset

        shared = set(marketplace_listing_queryset(None).values_list("id", flat=True))
        self.assertEqual(self._browse(None), shared)
        self.assertIn(self.other_campus.pk, shared)
        self.assertNotIn(self.flagged.pk, shared)

    def test_status_filter_cannot_reach_outside_the_marketplace(self) -> None:
        # Before, ?status=picked_up (or expired) listed other people's closed
        # listings, because browse had its own idea of what is visible.
        self.assertEqual(self._browse(self.viewer, status="picked_up"), set())
        self.assertEqual(self._browse(self.viewer, status="expired"), set())
        self.assertEqual(self._browse(self.viewer, status="reserved"), {self.reserved.pk})
        self.assertEqual(self._browse(self.viewer, status="active"), {self.visible.pk, self.reserved.pk})

    def test_staff_still_see_flagged_listings(self) -> None:
        staff = make_user("staff@alder.test", campus=self.campus_a, is_staff=True)
        self.assertIn(self.flagged.pk, self._browse(staff))
        self.assertNotIn(self.flagged.pk, self._browse(self.viewer))

    def test_campus_override_is_for_guests_staff_and_campus_managers(self) -> None:
        self.assertEqual(self._browse(None, campus="birch"), {self.other_campus.pk})
        # An ordinary signed-in user stays on their own campus.
        self.assertEqual(self._browse(self.viewer, campus="birch"), {self.visible.pk, self.reserved.pk})
        manager = make_user("manager@alder.test", campus=self.campus_a, is_campus_manager=True)
        self.assertEqual(self._browse(manager, campus="birch"), {self.other_campus.pk})

    def test_my_listings_still_show_everything_the_owner_has(self) -> None:
        mine = self._browse(self.good_owner, mine="1")
        self.assertTrue({self.visible.pk, self.flagged.pk, self.expired.pk, self.picked_up.pk} <= mine)
        self.assertNotIn(self.demo.pk, mine)
        self.assertNotIn(self.deleted.pk, mine)
        self.assertEqual(
            self._browse(self.good_owner, mine="1", status="picked_up"), {self.picked_up.pk}
        )

    def test_filters_and_sorts_still_apply(self) -> None:
        cheap = make_listing(
            self.good_owner,
            title="Cheap shelf",
            category=Listing.Category.STORAGE,
            price_type=Listing.PriceType.LOW_COST,
            price_amount=Decimal("3.00"),
        )
        self.assertEqual(self._browse(self.viewer, category="storage"), {cheap.pk})
        self.assertEqual(self._browse(self.viewer, price_type="low_cost"), {cheap.pk})
        self.assertEqual(self._browse(self.viewer, search="shelf"), {cheap.pk})

        self.client.force_authenticate(self.viewer)
        response = self.client.get(reverse("listings-list"), {"sort": "price_high"})
        self.assertEqual(response.data["results"][0]["id"], cheap.pk)


# ---------------------------------------------------------------------------
# L11: junk input is a 400, never a 500
# ---------------------------------------------------------------------------

class InputHandlingTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner-input@example.test")
        self.listing = make_listing(self.owner)
        self.client.force_authenticate(self.owner)

    def test_boost_amount_junk_is_400(self) -> None:
        response = self.client.post(
            reverse("listing-boost", kwargs={"pk": self.listing.pk}),
            {"amount_cents": "lots"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_donate_intent_junk_is_400(self) -> None:
        url = reverse("listing-donate-intent", kwargs={"pk": self.listing.pk})
        for body in (
            {"organization_id": 1, "percentage": "half"},
            {"organization_id": "the-good-one", "percentage": 10},
            {"organization_id": 1, "percentage": 250},
        ):
            self.assertEqual(self.client.post(url, body, format="json").status_code, 400, body)

    def _scan_session(self) -> tuple[RoomScanSession, RoomScanItemDraft]:
        session = RoomScanSession.objects.create(
            owner=self.owner,
            name="Sweep",
            room_label="North 204",
            pickup_zone="Old zone",
            move_out_deadline=timezone.now() + timedelta(days=4),
        )
        item = RoomScanItemDraft.objects.create(
            scan_session=session,
            title="Fan",
            category=Listing.Category.COMFORT,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
        )
        return session, item

    def test_ai_detect_status_junk_image_id_is_400(self) -> None:
        session, _item = self._scan_session()
        response = self.client.get(
            reverse("scan-sessions-ai-detect-status", kwargs={"pk": session.pk}),
            {"image_id": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    def test_batch_update_rejects_junk_item_ids(self) -> None:
        session, _item = self._scan_session()
        url = reverse("scan-sessions-batch-update", kwargs={"pk": session.pk})
        for item_ids in (["abc"], [{"id": 1}], "12"):
            response = self.client.patch(
                url, {"item_ids": item_ids, "changes": {"notes": "x"}}, format="json"
            )
            self.assertEqual(response.status_code, 400, item_ids)
        response = self.client.patch(
            url, {"item_ids": [_item.pk], "changes": ["notes"]}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_batch_update_is_all_or_nothing(self) -> None:
        session, item = self._scan_session()
        response = self.client.patch(
            reverse("scan-sessions-batch-update", kwargs={"pk": session.pk}),
            {
                "item_ids": [item.pk],
                "changes": {
                    "pickup_zone": "New zone",
                    "price_type": "low_cost",
                    "price_amount": "-5.00",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        session.refresh_from_db()
        # The session change must not survive the rejected item change.
        self.assertEqual(session.pickup_zone, "Old zone")

    def test_bulk_update_validates_pickup_zone_against_the_column(self) -> None:
        url = reverse("listings-bulk-update")
        too_long = self.client.patch(url, {"pickup_zone": "z" * 150}, format="json")
        self.assertEqual(too_long.status_code, 400)
        blank = self.client.patch(url, {"pickup_zone": "   "}, format="json")
        self.assertEqual(blank.status_code, 400)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.pickup_zone, "Maple Hall lobby")

        ok = self.client.patch(url, {"pickup_zone": "  Pine Hall desk  "}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.pickup_zone, "Pine Hall desk")

    def test_matching_a_request_with_a_junk_listing_id_is_400(self) -> None:
        seeker = make_user("seeker-input@example.test")
        rescue_request = RescueRequest.objects.create(
            seeker=seeker,
            title="Need a lamp",
            category=Listing.Category.LIGHTING,
            pickup_zone="Library",
            needed_by=timezone.now() + timedelta(days=2),
        )
        response = self.client.post(
            reverse("requests-match", kwargs={"pk": rescue_request.pk}),
            {"listing": "my-lamp"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_push_subscribe_with_wrong_types_is_400(self) -> None:
        for body in (
            {"endpoint": "https://push.example.test/abc", "keys": ["p256dh", "auth"]},
            {"endpoint": ["https://push.example.test/abc"], "keys": {"p256dh": "k", "auth": "a"}},
            {"platform": "fcm", "token": 12345.5},
        ):
            response = self.client.post(reverse("push-subscribe"), body, format="json")
            self.assertIn(response.status_code, (200, 400), body)
        wrong_keys = self.client.post(
            reverse("push-subscribe"),
            {"endpoint": "https://push.example.test/abc", "keys": ["p256dh", "auth"]},
            format="json",
        )
        self.assertEqual(wrong_keys.status_code, 400)
        unsubscribe = self.client.post(
            reverse("push-unsubscribe"), {"endpoint": {"nested": True}}, format="json"
        )
        self.assertEqual(unsubscribe.status_code, 400)

    def test_stripe_error_text_is_not_returned_from_boost(self) -> None:
        stripe = mock.Mock()
        stripe.PaymentIntent.create.side_effect = Exception("No such customer: cus_SECRET123")
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}), mock.patch(
            "listings.views.revenue._stripe", return_value=stripe
        ), self.assertLogs("listings.views.revenue", level="ERROR"):
            response = self.client.post(
                reverse("listing-boost", kwargs={"pk": self.listing.pk}),
                {"amount_cents": 100},
                format="json",
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("cus_SECRET123", json.dumps(response.data))

    def test_stripe_error_text_is_not_returned_from_subscribe(self) -> None:
        from .views import revenue

        campus = Campus.objects.create(name="Alder University", slug="alder")
        manager = make_user("billing@alder.test", campus=campus, is_campus_manager=True)
        self.client.force_authenticate(manager)
        stripe = mock.Mock()
        stripe.Customer.create.side_effect = Exception("Invalid API Key provided: sk_live_LEAK")
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}), mock.patch.dict(
            revenue._TIER_PRICE_IDS, {"standard": "price_123"}
        ), mock.patch.object(revenue, "_stripe", return_value=stripe), self.assertLogs(
            "listings.views.revenue", level="ERROR"
        ):
            response = self.client.post(
                reverse("campus-subscribe"), {"tier": "standard"}, format="json"
            )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("sk_live_LEAK", json.dumps(response.data))


# ---------------------------------------------------------------------------
# L12: small correctness items
# ---------------------------------------------------------------------------

class SmallCorrectnessTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        from .services import confirm_reservation

        self.owner = make_user("owner-small@example.test")
        self.claimant = make_user("claimant-small@example.test")
        self.listing = make_listing(self.owner)
        self.reservation = confirm_reservation(
            make_reservation(self.listing, self.claimant), notify=False
        )

    def test_thank_you_notification_has_a_type(self) -> None:
        Reservation.objects.filter(pk=self.reservation.pk).update(
            status=Reservation.Status.COMPLETED
        )
        self.client.force_authenticate(self.claimant)
        response = self.client.post(
            reverse("reservation-thank", kwargs={"pk": self.reservation.pk}), {}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.data)
        notification = Notification.objects.get(dedupe_key=f"thankyou:{self.reservation.pk}")
        self.assertIn(notification.type, Notification.Type.values)

    def test_slot_confirmation_notifies_again_after_a_reproposal(self) -> None:
        slots_url = reverse("reservation-slots", kwargs={"pk": self.reservation.pk})
        first = (timezone.now() + timedelta(days=1)).isoformat()
        second = (timezone.now() + timedelta(days=2)).isoformat()

        def propose() -> list[str]:
            self.client.force_authenticate(self.owner)
            response = self.client.patch(slots_url, {"slots": [first, second]}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            return response.data["pickup_slots"]

        def confirm(slot: str) -> None:
            self.client.force_authenticate(self.claimant)
            response = self.client.patch(slots_url, {"confirm_slot": slot}, format="json")
            self.assertEqual(response.status_code, 200, response.data)

        def unread_confirmations() -> int:
            return Notification.objects.filter(
                user=self.owner, title="Pickup time confirmed", is_read=False
            ).count()

        proposed = propose()
        confirm(proposed[0])
        self.assertEqual(unread_confirmations(), 1)
        Notification.objects.filter(user=self.owner).update(is_read=True)

        # The owner changes their mind; the claimant picks the other time.
        proposed = propose()
        confirm(proposed[1])
        self.assertEqual(unread_confirmations(), 1)

    def test_partner_usage_needs_a_scope_and_counts_against_the_quota(self) -> None:
        campus = Campus.objects.create(name="Alder University", slug="alder")
        client = APIClient()
        url = reverse("partner-usage")

        _unscoped, raw_unscoped = PartnerAPIKey.generate(
            campus=campus, partner_name="No scopes", scopes=[]
        )
        refused = client.get(url, HTTP_AUTHORIZATION=f"Bearer {raw_unscoped}")
        self.assertEqual(refused.status_code, 403)

        key, raw = PartnerAPIKey.generate(
            campus=campus, partner_name="Listings only", scopes=["listing_read"]
        )
        PartnerAPIKey.objects.filter(pk=key.pk).update(rate_limit_per_day=1)
        first = client.get(url, HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["listing_count"], 0)
        # Not scoped for reservations or webhooks, so those figures are withheld.
        self.assertIsNone(first.data["reservation_count"])
        self.assertIsNone(first.data["webhook_endpoints"])
        second = client.get(url, HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(second.status_code, 429)

    def test_no_route_is_registered_twice(self) -> None:
        from . import urls

        routes = [str(pattern.pattern) for pattern in urls.urlpatterns]
        names = [pattern.name for pattern in urls.urlpatterns]
        self.assertEqual(sorted(routes), sorted(set(routes)))
        self.assertEqual(sorted(names), sorted(set(names)))

    def _open_dispute(self, user: Any) -> Any:
        self.client.force_authenticate(user)
        return self.client.post(
            reverse("reservation-dispute", kwargs={"pk": self.reservation.pk}),
            {"reason": "The item was not as described."},
            format="json",
        )

    def test_one_dispute_per_reservation_and_opener(self) -> None:
        self.assertEqual(self._open_dispute(self.claimant).status_code, 201)
        self.assertEqual(self._open_dispute(self.claimant).status_code, 409)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.trust_strikes, 1)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Dispute.objects.create(
                reservation=self.reservation, opened_by=self.claimant, reason="again"
            )
        # The other participant may still open their own.
        self.assertEqual(self._open_dispute(self.owner).status_code, 201)

    def test_dispute_on_someone_elses_reservation_is_404(self) -> None:
        stranger = make_user("stranger-small@example.test")
        self.assertEqual(self._open_dispute(stranger).status_code, 404)
        self.assertFalse(Dispute.objects.exists())

    def _close_dispute(self, action: str) -> Any:
        dispute = Dispute.objects.get(reservation=self.reservation)
        self.client.force_authenticate(make_user(f"mod-{action}@example.test", is_staff=True))
        return self.client.patch(
            reverse("dispute-detail", kwargs={"pk": dispute.pk}), {"action": action}, format="json"
        )

    def test_dismissing_a_dispute_gives_the_strike_back(self) -> None:
        self._open_dispute(self.claimant)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.trust_strikes, 1)

        self.assertEqual(self._close_dispute("dismiss").status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.trust_strikes, 0)

    def test_dismissal_never_takes_strikes_below_zero(self) -> None:
        self._open_dispute(self.claimant)
        User.objects.filter(pk=self.owner.pk).update(trust_strikes=0)
        self.assertEqual(self._close_dispute("dismiss").status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.trust_strikes, 0)

    def test_resolving_a_dispute_keeps_the_strike(self) -> None:
        self._open_dispute(self.claimant)
        self.assertEqual(self._close_dispute("resolve").status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.trust_strikes, 1)


# ---------------------------------------------------------------------------
# L13: AI helper views
# ---------------------------------------------------------------------------

class AiHelperViewTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = make_user("ai-user@example.test")
        self.client.force_authenticate(self.user)

    def _model_answering(self, text: str) -> Any:
        part = SimpleNamespace(text=text, function_call=None)
        response = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason="STOP")]
        )
        models = mock.Mock()
        models.generate_content.return_value = response
        return SimpleNamespace(models=models)

    def _parse(self, client: Any, query: str = "posters for my wall") -> Any:
        from . import ai_client

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), mock.patch.object(
            ai_client, "_client", return_value=client
        ):
            return self.client.post(
                reverse("listings-parse-search"), {"query": query}, format="json"
            )

    def test_parse_search_accepts_every_listing_category(self) -> None:
        fake = self._model_answering(
            '{"search": "posters", "category": "decor", "price_type": null, "pickup_zone_hint": null}'
        )
        response = self._parse(fake)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["category"], "decor")
        self.assertIn(
            {"label": "Category: Decor", "field": "category", "value": "decor"},
            response.data["chips"],
        )

    def test_parse_search_goes_through_the_shared_json_helper(self) -> None:
        fake = self._model_answering(
            'Here you go:\n```json\n{"search": "lamp", "category": "lighting", "price_type": "free", '
            '"pickup_zone_hint": {"nested": "junk"}}\n```'
        )
        response = self._parse(fake, query="free lamp")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["search"], "lamp")
        self.assertEqual(response.data["price_type"], "free")
        # A non-string value from the model is dropped, not echoed back.
        self.assertIsNone(response.data["pickup_zone_hint"])
        config = fake.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config["response_mime_type"], "application/json")

    def test_every_model_backed_view_is_throttled_for_all_methods(self) -> None:
        from .throttles import AiAssistThrottle, WriteOnlyUserRateThrottle
        from .views import listings as listing_views

        for view in (
            listing_views.ParseSearchView,
            listing_views.GenerateDescriptionView,
            listing_views.RescueSuggestionsView,
            listing_views.VisionAutofillView,
        ):
            throttles = view.throttle_classes
            self.assertTrue(
                any(issubclass(throttle, AiAssistThrottle) for throttle in throttles), view.__name__
            )
            self.assertFalse(
                any(issubclass(throttle, WriteOnlyUserRateThrottle) for throttle in throttles),
                view.__name__,
            )

    def test_throttle_rate_does_not_depend_on_a_settings_entry(self) -> None:
        from rest_framework.settings import api_settings

        from . import throttles

        for name in (
            "AiAssistThrottle",
            "ParseSearchThrottle",
            "GenerateDescriptionThrottle",
            "VisionAutofillThrottle",
            "RescueSuggestionsThrottle",
        ):
            throttle_class = getattr(throttles, name)
            self.assertNotIn(throttle_class.scope, api_settings.DEFAULT_THROTTLE_RATES)
            throttle = throttle_class()  # would raise ImproperlyConfigured without `rate`
            self.assertGreater(throttle.num_requests, 0)

    def test_the_get_that_calls_a_model_is_rate_limited(self) -> None:
        from .throttles import RescueSuggestionsThrottle

        url = reverse("listings-rescue-suggestions")
        with mock.patch.object(RescueSuggestionsThrottle, "rate", "2/hour"):
            statuses = [self.client.get(url).status_code for _ in range(3)]
        self.assertNotIn(429, statuses[:2])
        self.assertEqual(statuses[2], 429)

    def test_generate_description_rejects_a_junk_retail_value(self) -> None:
        response = self.client.post(
            reverse("listings-generate-description"),
            {"title": "Desk lamp", "estimated_retail_value": "a lot"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------------------
# L14: image_cdn_url must be this listing's own upload
# ---------------------------------------------------------------------------

@override_settings(MEDIA_CDN_BASE_URL="https://cdn.renest.test")
class ImageCdnUrlTests(CoreTestCase):
    KEY = "0123456789abcdef0123456789abcdef"

    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner-cdn@example.test")
        self.listing = make_listing(self.owner)
        self.other_listing = make_listing(make_user("other-cdn@example.test"))
        self.client.force_authenticate(self.owner)

    def _set(self, url: str) -> Any:
        return self.client.patch(
            reverse("listings-detail", kwargs={"pk": self.listing.pk}),
            {"image_cdn_url": url},
            format="json",
        )

    def test_this_listings_own_upload_is_accepted(self) -> None:
        url = f"https://cdn.renest.test/listing-images/{self.listing.pk}/{self.KEY}.jpg"
        response = self._set(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["image_url"], url)

    def test_another_listings_upload_is_rejected(self) -> None:
        response = self._set(
            f"https://cdn.renest.test/listing-images/{self.other_listing.pk}/{self.KEY}.jpg"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("image_cdn_url", response.data)

    def test_other_objects_on_the_same_host_are_rejected(self) -> None:
        own_prefix = f"https://cdn.renest.test/listing-images/{self.listing.pk}"
        for url in (
            "https://cdn.renest.test/donation-receipts/receipt-1.pdf",
            f"{own_prefix}/../{self.other_listing.pk}/{self.KEY}.jpg",
            f"{own_prefix}/{self.KEY}.jpg?redirect=https://tracker.test/pixel.gif",
            f"{own_prefix}0/{self.KEY}.jpg",
            f"https://cdn.renest.test.evil.test/listing-images/{self.listing.pk}/{self.KEY}.jpg",
        ):
            self.assertEqual(self._set(url).status_code, 400, url)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.image_cdn_url, "")

    def test_it_cannot_be_set_before_the_listing_exists(self) -> None:
        response = self.client.post(
            reverse("listings-list"),
            {
                "title": "Mini fan",
                "description": "Quiet, clean, and still in the box.",
                "category": Listing.Category.COMFORT,
                "condition": Listing.Condition.NEW,
                "price_type": Listing.PriceType.FREE,
                "pickup_zone": "Elm Hall",
                "available_until": (timezone.now() + timedelta(days=1)).isoformat(),
                "image_cdn_url": f"https://cdn.renest.test/listing-images/1/{self.KEY}.jpg",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("image_cdn_url", response.data)


# ---------------------------------------------------------------------------
# L15: re-running AI detection must not answer with the previous result
# ---------------------------------------------------------------------------

class AiDetectRerunTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        from .tests import jpeg_upload

        self.owner = make_user("owner-detect@example.test")
        self.client.force_authenticate(self.owner)
        self.session = RoomScanSession.objects.create(
            owner=self.owner,
            name="Dorm scan",
            room_label="North 204",
            pickup_zone="Lobby",
            move_out_deadline=timezone.now() + timedelta(days=5),
        )
        self.image = RoomScanImage.objects.create(
            scan_session=self.session, image=jpeg_upload(), position=0
        )

    def _rerun_over(self, previous: dict[str, Any]) -> tuple[Any, Any, Any]:
        from .tasks import ai_detect_cache_key

        key = ai_detect_cache_key(self.session.pk, self.image.pk)
        cache.set(key, previous, timeout=600)
        # delay() is replaced: with a real worker the job has not finished
        # by the time the view answers.
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), mock.patch(
            "listings.tasks.run_ai_detection.delay"
        ) as delay:
            response = self.client.post(
                reverse("scan-sessions-ai-detect", kwargs={"pk": self.session.pk}),
                {"image_id": self.image.pk},
                format="json",
            )
        return response, delay, cache.get(key)

    def test_rerun_after_a_finished_run_reports_processing(self) -> None:
        response, delay, cached = self._rerun_over({"status": "done", "created_count": 3})
        delay.assert_called_once()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data, {"status": "processing"})
        self.assertEqual(cached, {"status": "processing"})

    def test_rerun_after_a_failed_run_reports_processing(self) -> None:
        response, delay, cached = self._rerun_over({"status": "error", "detail": "old failure"})
        delay.assert_called_once()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(cached, {"status": "processing"})

    def test_a_run_in_progress_is_not_dispatched_twice(self) -> None:
        response, delay, _cached = self._rerun_over({"status": "processing"})
        delay.assert_not_called()
        self.assertEqual(response.status_code, 202)


# ---------------------------------------------------------------------------
# L16: edits are moderated again
# ---------------------------------------------------------------------------

class RemoderationOnEditTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner-mod@example.test")
        self.listing = make_listing(
            self.owner, moderation_status=Listing.ModerationStatus.APPROVED
        )
        self.url = reverse("listings-detail", kwargs={"pk": self.listing.pk})
        self.client.force_authenticate(self.owner)

    def test_changing_screened_fields_schedules_moderation(self) -> None:
        for change in (
            {"title": "Totally different title"},
            {"description": "A new description that says something else."},
            {"category": Listing.Category.DECOR},
            {"price_type": Listing.PriceType.LOW_COST, "price_amount": "4.00"},
        ):
            with mock.patch("listings.tasks.remoderate_listing_task.delay") as delay:
                response = self.client.patch(self.url, change, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            delay.assert_called_once_with(self.listing.pk)

    def test_an_edit_that_changes_nothing_screened_is_left_alone(self) -> None:
        with mock.patch("listings.tasks.remoderate_listing_task.delay") as delay:
            response = self.client.patch(self.url, {"pickup_zone": "Pine Hall desk"}, format="json")
            same = self.client.patch(self.url, {"title": self.listing.title}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(same.status_code, 200, same.data)
        delay.assert_not_called()

    def test_a_scheduling_failure_does_not_fail_the_edit(self) -> None:
        with mock.patch(
            "listings.tasks.remoderate_listing_task.delay", side_effect=RuntimeError("broker down")
        ), self.assertLogs("listings.views.listings", level="ERROR"):
            response = self.client.patch(self.url, {"title": "Renamed lamp"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, "Renamed lamp")

    def test_the_response_shows_a_verdict_reached_inline(self) -> None:
        def screened_inline(listing_pk: int) -> None:
            # Without a worker the task runs inside delay() and may flag.
            Listing.objects.filter(pk=listing_pk).update(
                moderation_status=Listing.ModerationStatus.FLAGGED,
                moderation_flag_reason="Prohibited item.",
            )

        with mock.patch("listings.tasks.remoderate_listing_task.delay", side_effect=screened_inline):
            response = self.client.patch(self.url, {"title": "Something else"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["moderation_status"], "flagged")
        self.assertEqual(response.data["moderation_flag_reason"], "Prohibited item.")

    def test_editing_does_not_clear_a_moderators_flag(self) -> None:
        # End to end, with the real task: a flag set by staff survives an edit.
        Listing.objects.filter(pk=self.listing.pk).update(
            moderation_status=Listing.ModerationStatus.FLAGGED,
            moderation_flag_reason="Reviewed by staff.",
        )
        response = self.client.patch(self.url, {"title": "Harmless new title"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.moderation_status, Listing.ModerationStatus.FLAGGED)


# ---------------------------------------------------------------------------
# L17: viewing a listing has bounded side effects
# ---------------------------------------------------------------------------

class ListingViewInteractionTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.viewer = make_user("viewer-interaction@example.test")
        self.listing = make_listing(make_user("owner-interaction@example.test"))
        self.url = reverse("listings-detail", kwargs={"pk": self.listing.pk})
        self.client.force_authenticate(self.viewer)

    def _views(self) -> int:
        return ListingInteractionEvent.objects.filter(
            user=self.viewer, listing=self.listing, action=ListingInteractionEvent.Action.VIEW
        ).count()

    def test_repeat_views_record_one_interaction_per_day(self) -> None:
        for _ in range(4):
            self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self._views(), 1)

    def test_a_repeat_view_does_not_count_the_users_whole_history(self) -> None:
        self.client.get(self.url)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(self.url)
        counting = [
            query["sql"]
            for query in queries
            if "listinginteractionevent" in query["sql"].lower() and "count(" in query["sql"].lower()
        ]
        self.assertEqual(counting, [])

    def test_a_view_on_another_day_is_recorded_again(self) -> None:
        self.client.get(self.url)
        tomorrow = timezone.localdate() + timedelta(days=1)
        with mock.patch("listings.views.listings.timezone.localdate", return_value=tomorrow):
            self.client.get(self.url)
        self.assertEqual(self._views(), 2)


# ---------------------------------------------------------------------------
# L18: boost redemption reads boosted_until under a lock
# ---------------------------------------------------------------------------

class BoostRedemptionTests(CoreTestCase):
    def test_a_boost_extends_what_a_concurrent_payment_just_committed(self) -> None:
        owner = make_user("owner-boost@example.test")
        listing = make_listing(owner)
        other_payment_until = timezone.now() + timedelta(hours=24)

        def retrieve(_payment_intent_id: str) -> Any:
            # Another payment for the same listing commits while this request
            # is still talking to Stripe, after it loaded the listing.
            Listing.objects.filter(pk=listing.pk).update(boosted_until=other_payment_until)
            return SimpleNamespace(
                status="succeeded",
                amount=100,
                metadata={"listing_id": str(listing.pk), "type": "listing_boost"},
            )

        stripe = mock.Mock()
        stripe.PaymentIntent.retrieve.side_effect = retrieve
        self.client.force_authenticate(owner)
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}), mock.patch(
            "listings.views.revenue._stripe", return_value=stripe
        ):
            response = self.client.post(
                reverse("listing-boost-confirm", kwargs={"pk": listing.pk}),
                {"payment_intent_id": "pi_second"},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        listing.refresh_from_db()
        # Both paid days count: 24h from the other payment plus 24h from this one.
        assert listing.boosted_until is not None
        self.assertGreaterEqual(listing.boosted_until, other_payment_until + timedelta(hours=24))


# ---------------------------------------------------------------------------
# L19: full-text search is backed by the GIN index
# ---------------------------------------------------------------------------

class FullTextSearchTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.viewer = make_user("viewer-search@example.test")
        self.owner = make_user("owner-search@example.test")
        self.client.force_authenticate(self.viewer)

    def test_search_finds_a_listing_by_title_description_or_zone(self) -> None:
        by_title = make_listing(self.owner, title="Vintage typewriter")
        by_description = make_listing(
            self.owner, title="Old machine", description="A mechanical typewriter with a new ribbon."
        )
        by_zone = make_listing(self.owner, title="Chair", pickup_zone="Typewriter museum steps")
        make_listing(self.owner, title="Unrelated rug")
        response = self.client.get(reverse("listings-list"), {"search": "typewriter"})
        self.assertEqual(result_ids(response), {by_title.pk, by_description.pk, by_zone.pk})

    @skipUnless(IS_POSTGRES, "full-text search and the GIN index exist only on PostgreSQL")
    def test_search_stems_words_and_ranks_title_matches_first(self) -> None:
        in_description = make_listing(
            self.owner, title="Old machine", description="Comes with two typewriters."
        )
        in_title = make_listing(self.owner, title="Typewriter", description="Works.")
        response = self.client.get(
            reverse("listings-list"), {"search": "typewriters", "sort": "relevance"}
        )
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, [in_title.pk, in_description.pk])

    @skipUnless(IS_POSTGRES, "full-text search and the GIN index exist only on PostgreSQL")
    def test_search_filter_is_the_expression_the_gin_index_covers(self) -> None:
        make_listing(self.owner, title="Vintage typewriter")
        make_listing(self.owner, title="Unrelated rug")

        # 1. Browse filters with the match operator, and no longer builds a
        #    vector for every row just to compare its rank with zero.
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("listings-list"), {"search": "typewriters"})
        self.assertEqual(response.data["count"], 1)
        browse_sql = [query["sql"] for query in queries if "to_tsvector" in query["sql"]]
        self.assertTrue(browse_sql)
        for sql in browse_sql:
            self.assertIn(" @@ ", sql)
            self.assertNotIn("ts_rank", sql)

        # 2. The predicate, on its own, can be answered from the index.
        #    all_objects and order_by() leave nothing else in the query, and
        #    with sequential scans ruled out the only remaining way to
        #    evaluate it is an index whose expression matches exactly. A
        #    mismatch (other config, other columns, weights) shows a Seq Scan.
        from django.contrib.postgres.search import SearchQuery

        from .views.listings import _FTS_CONFIG, _fts_document

        bare = (
            Listing.all_objects.alias(document=_fts_document())
            .filter(document=SearchQuery("typewriters", config=_FTS_CONFIG, search_type="websearch"))
            .order_by()
        )
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(bare.count(), 1)
        predicate = queries[-1]["sql"].split(" WHERE ", 1)[1]
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute('EXPLAIN SELECT "id" FROM "listings_listing" WHERE ' + predicate)
            plan = "\n".join(row[0] for row in cursor.fetchall())
            cursor.execute("RESET enable_seqscan")
        self.assertIn("Bitmap Index Scan on listings_listing_fts_gin", plan)
        self.assertNotIn("Seq Scan", plan)

        # 3. And browse uses that very predicate, not a look-alike.
        for sql in browse_sql:
            self.assertIn(predicate, sql)


# ---------------------------------------------------------------------------
# L20: data integrity
# ---------------------------------------------------------------------------

class DataIntegrityTests(CoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner-integrity@example.test")
        self.claimant = make_user("claimant-integrity@example.test")
        self.listing = make_listing(self.owner)

    def test_negative_money_is_rejected_by_the_database(self) -> None:
        for field in ("price_amount", "estimated_retail_value"):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Listing.objects.filter(pk=self.listing.pk).update(**{field: Decimal("-1.00")})

    def test_rating_outside_one_to_five_is_rejected_by_the_database(self) -> None:
        reservation = make_reservation(self.listing, self.claimant)
        for rating in (0, 6):
            with self.assertRaises(IntegrityError), transaction.atomic():
                HandoffFeedback.objects.create(
                    reservation=reservation,
                    reviewer=self.claimant,
                    reviewee=self.owner,
                    rating=rating,
                )

    def test_queryset_delete_is_a_soft_delete(self) -> None:
        reservation = make_reservation(self.listing, self.claimant)
        deleted, _detail = Listing.objects.filter(pk=self.listing.pk).delete()
        self.assertEqual(deleted, 1)
        self.assertFalse(Listing.objects.filter(pk=self.listing.pk).exists())
        row = Listing.all_objects.get(pk=self.listing.pk)
        self.assertIsNotNone(row.deleted_at)
        # Nothing cascaded.
        self.assertTrue(Reservation.objects.filter(pk=reservation.pk).exists())

    def test_related_manager_delete_is_soft_too(self) -> None:
        self.owner.listings.all().delete()
        self.assertTrue(Listing.all_objects.filter(pk=self.listing.pk).exists())

    def test_real_deletes_remain_available(self) -> None:
        second = make_listing(self.owner, title="Second")
        Listing.objects.filter(pk=self.listing.pk).hard_delete()
        self.assertFalse(Listing.all_objects.filter(pk=self.listing.pk).exists())
        Listing.all_objects.filter(pk=second.pk).delete()
        self.assertFalse(Listing.all_objects.filter(pk=second.pk).exists())

    def _admin(self) -> None:
        admin_user = User.objects.create_superuser(
            email="admin-integrity@example.test", password="pw-Str0ng!x", display_name="Admin"
        )
        self.client.force_login(admin_user)

    def test_admin_shows_soft_deleted_listings(self) -> None:
        make_listing(self.owner, title="Removed by its owner", deleted_at=timezone.now())
        self._admin()
        response = self.client.get(reverse("admin:listings_listing_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Removed by its owner")

    def test_admin_bulk_delete_is_soft_and_releases_the_claim(self) -> None:
        reservation = make_reservation(self.listing, self.claimant)
        self._admin()
        response = self.client.post(
            reverse("admin:listings_listing_changelist"),
            {"action": "delete_selected", "_selected_action": [self.listing.pk], "post": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Listing.all_objects.get(pk=self.listing.pk).deleted_at)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)


# ---------------------------------------------------------------------------
# Migrations 0041 and 0042: existing rows are brought in line first
# ---------------------------------------------------------------------------

class IntegrityMigrationTests(TransactionTestCase):
    before = [("listings", "0040_listingboostpayment")]
    constraints = ("listings", "0042_integrity_constraints")

    def _migrate_back(self) -> Any:
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        # Historical models for listings before the clean-up, and for every
        # other app at its latest migration, which is what the tables match.
        other_apps = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "listings"]
        return executor.loader.project_state(other_apps + self.before).apps

    def _migrate_forward(self) -> None:
        # To the newest migration of every app, so the current models used
        # for the assertions match the schema whatever is added after 0042.
        executor = MigrationExecutor(connection)
        self.assertIn(self.constraints, executor.loader.graph.nodes)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_rows_are_cleaned_up_before_the_constraints_apply(self) -> None:
        self.addCleanup(self._migrate_forward)
        old = self._migrate_back()

        OldUser = old.get_model("accounts", "User")
        OldListing = old.get_model("listings", "Listing")
        OldReservation = old.get_model("listings", "Reservation")
        OldDispute = old.get_model("listings", "Dispute")
        OldHold = old.get_model("listings", "StorageHold")
        OldFeedback = old.get_model("listings", "HandoffFeedback")
        OldHub = old.get_model("hubs", "DonationHub")

        owner = OldUser.objects.create(email="owner-mig@example.test", display_name="Owner", trust_strikes=2)
        claimant = OldUser.objects.create(email="claimant-mig@example.test", display_name="Claimant")
        staff = OldUser.objects.create(email="staff-mig@example.test", display_name="Staff")
        hub = OldHub.objects.create(
            name="Hub", campus_name="Alder", zone_label="Union", description="d", open_instructions="o"
        )

        def listing(title: str, **extra: Any) -> Any:
            return OldListing.objects.create(
                owner=owner,
                title=title,
                description="d",
                category="other",
                condition="good",
                price_type="free",
                pickup_zone="Lobby",
                available_until=timezone.now() + timedelta(days=2),
                **extra,
            )

        negative = listing("Negative", price_amount=Decimal("-5.00"), estimated_retail_value=Decimal("-1.00"))
        confirmed_listing = listing("Confirmed")
        completed_listing = listing("Completed")
        double_hold_listing = listing("Held twice")
        lapsed_hold_listing = listing("Hold lapsed")

        confirmed = OldReservation.objects.create(
            listing=confirmed_listing, claimant=claimant, pickup_time_window="w", status="confirmed"
        )
        completed = OldReservation.objects.create(
            listing=completed_listing,
            claimant=claimant,
            pickup_time_window="w",
            status="completed",
            handoff_pin="123456",
        )
        OldFeedback.objects.create(reservation=completed, reviewer=claimant, reviewee=owner, rating=9)
        for _ in range(2):
            OldDispute.objects.create(reservation=completed, opened_by=claimant, reason="twice")

        future = timezone.now() + timedelta(days=1)
        first_hold = OldHold.objects.create(
            listing=double_hold_listing, hub=hub, held_by=staff, expires_at=future
        )
        OldHold.objects.create(listing=double_hold_listing, hub=hub, held_by=staff, expires_at=future)
        lapsed_hold = OldHold.objects.create(
            listing=lapsed_hold_listing,
            hub=hub,
            held_by=staff,
            expires_at=timezone.now() - timedelta(days=1),
        )

        self._migrate_forward()

        negative_now = Listing.all_objects.get(pk=negative.pk)
        self.assertEqual(negative_now.price_amount, Decimal("0.00"))
        self.assertEqual(negative_now.estimated_retail_value, Decimal("0.00"))
        self.assertEqual(HandoffFeedback.objects.get(reservation_id=completed.pk).rating, 5)

        # One dispute survives and the duplicate's strike is handed back.
        self.assertEqual(Dispute.objects.filter(reservation_id=completed.pk).count(), 1)
        self.assertEqual(User.objects.get(pk=owner.pk).trust_strikes, 1)

        active = StorageHold.objects.filter(status=StorageHold.Status.ACTIVE)
        self.assertEqual(list(active.values_list("pk", flat=True)), [first_hold.pk])
        self.assertEqual(
            StorageHold.objects.get(pk=lapsed_hold.pk).status, StorageHold.Status.RELEASED
        )

        # A confirmed handoff without a PIN could never be completed now.
        pin = Reservation.objects.get(pk=confirmed.pk).handoff_pin
        self.assertRegex(pin, r"^\d{6}$")
        self.assertEqual(Reservation.objects.get(pk=completed.pk).handoff_pin, "")
