"""Regression tests for privacy leaks and abuse paths found in the production-readiness audit.

Each class is named after the rule that must hold and its docstring says what
used to go wrong.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from hubs.models import DonationHub, HubManager

from .models import (
    Listing,
    ListingReport,
    ListingViewEvent,
    Notification,
    Reservation,
    ReservationMessage,
    RoomScanItemDraft,
    RoomScanSession,
    StorageHold,
)

User = get_user_model()


def make_user(email: str, **extra: Any) -> Any:
    extra.setdefault("display_name", email.split("@")[0].title())
    extra.setdefault("email_verified", True)
    return User.objects.create_user(email=email, password="pw-Str0ng!x", **extra)


def make_listing(owner: Any, **extra: Any) -> Listing:
    fields: dict[str, Any] = {
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


class AuditTestCase(APITestCase):
    def setUp(self) -> None:
        # Throttle counters live in the cache and key on reused primary keys.
        cache.clear()
        self.addCleanup(cache.clear)


class ReporterStaysAnonymousToTheOwnerTests(AuditTestCase):
    """GET /api/trust/me used to hand the owner the reporter's name and their written details."""

    def test_owner_sees_the_report_but_not_who_filed_it(self) -> None:
        owner = make_user("owner@campus.test")
        reporter = make_user("whistle@campus.test", display_name="Wren Reporter")
        listing = make_listing(owner)
        ListingReport.objects.create(
            listing=listing,
            reporter=reporter,
            reason=ListingReport.Reason.INACCURATE,
            details="The photos are of a different lamp. Signed, Wren.",
        )

        self.client.force_authenticate(owner)
        response = self.client.get(reverse("trust-overview"))

        self.assertEqual(response.status_code, 200)
        (report,) = response.data["reports_on_my_listings"]
        self.assertEqual(
            set(report), {"id", "listing", "listing_title", "reason", "status", "created_at"}
        )
        self.assertNotIn("Wren", str(response.data["reports_on_my_listings"]))

    def test_reporter_still_sees_their_own_report_in_full(self) -> None:
        reporter = make_user("whistle@campus.test")
        listing = make_listing(make_user("owner@campus.test"))
        ListingReport.objects.create(
            listing=listing, reporter=reporter, reason=ListingReport.Reason.SPAM, details="Spam."
        )

        self.client.force_authenticate(reporter)
        (report,) = self.client.get(reverse("trust-overview")).data["reports_filed"]

        self.assertEqual(report["details"], "Spam.")


class ChatShowsDisplayNamesTests(AuditTestCase):
    """Pickup chat labelled each message with the sender's email local part.

    The name came from get_full_name(), which is always empty here, so the
    fallback (everything before the @) was shown to the other person.
    """

    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("jsmith24@campus.test", display_name="Jordan")
        self.claimant = make_user("priya.k@campus.test", display_name="Priya")
        self.reservation = Reservation.objects.create(
            listing=make_listing(self.owner), claimant=self.claimant, pickup_time_window="Fri 3-5pm"
        )
        self.url = reverse("reservations-messages", args=[self.reservation.pk])

    def test_message_and_notification_use_the_display_name(self) -> None:
        self.client.force_authenticate(self.owner)
        sent = self.client.post(self.url, {"body": "See you at five."}, format="json")
        self.assertEqual(sent.status_code, 201)

        self.client.force_authenticate(self.claimant)
        rows = self.client.get(self.url).data
        messages = rows["results"] if isinstance(rows, dict) else rows
        self.assertEqual([message["sender_name"] for message in messages], ["Jordan"])

        notification = Notification.objects.get(user=self.claimant, type=Notification.Type.CHAT)
        self.assertEqual(notification.title, "New message from Jordan")
        self.assertNotIn("jsmith24", str(messages) + notification.title)

    def test_en_route_message_uses_the_display_name(self) -> None:
        Reservation.objects.filter(pk=self.reservation.pk).update(status=Reservation.Status.CONFIRMED)
        self.client.force_authenticate(self.claimant)
        response = self.client.post(reverse("reservation-en-route", args=[self.reservation.pk]))
        self.assertEqual(response.status_code, 200)
        body = ReservationMessage.objects.get(reservation=self.reservation).body
        self.assertIn("Priya is on their way", body)
        self.assertNotIn("priya.k", body)


class AnonymousListingViewsTests(AuditTestCase):
    """Every cookieless GET of a listing created a session row and counted as a new view."""

    def setUp(self) -> None:
        super().setUp()
        self.listing = make_listing(make_user("owner@campus.test"))
        self.url = reverse("listings-detail", args=[self.listing.pk])

    def test_repeat_requests_without_cookies_count_once_and_create_no_session(self) -> None:
        for _ in range(5):
            response = APIClient().get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("sessionid", response.cookies)

        self.assertEqual(Session.objects.count(), 0)
        self.assertEqual(ListingViewEvent.objects.filter(listing=self.listing).count(), 1)

    def test_visitors_from_different_addresses_are_counted_separately(self) -> None:
        APIClient().get(self.url, REMOTE_ADDR="203.0.113.10")
        APIClient().get(self.url, REMOTE_ADDR="203.0.113.11")
        self.assertEqual(ListingViewEvent.objects.filter(listing=self.listing).count(), 2)

    def test_a_signed_in_visitor_is_counted_by_session(self) -> None:
        viewer = make_user("viewer@campus.test")
        client = APIClient()
        self.assertTrue(client.login(email=viewer.email, password="pw-Str0ng!x"))
        client.get(self.url)
        client.get(self.url)
        (event,) = ListingViewEvent.objects.filter(listing=self.listing)
        self.assertEqual(event.session_key, client.session.session_key)


class HubHoldScopeTests(AuditTestCase):
    """A hub manager could put any listing on the platform on hold, blocking every claim on it."""

    def setUp(self) -> None:
        super().setUp()
        self.hub = DonationHub.objects.create(
            name="Reuse Closet",
            campus_name="Pacific State",
            zone_label="Union East",
            description="Shelves.",
            open_instructions="Open daily.",
        )
        self.manager = make_user("manager@campus.test")
        HubManager.objects.create(hub=self.hub, user=self.manager)
        self.owner = make_user("owner@campus.test")
        self.client.force_authenticate(self.manager)

    def _hold(self, listing: Listing) -> Any:
        return self.client.post(
            reverse("logistics-holds"), {"listing_id": listing.pk, "hub_id": self.hub.pk}, format="json"
        )

    def test_unrelated_listing_cannot_be_held(self) -> None:
        listing = make_listing(self.owner)
        self.assertEqual(self._hold(listing).status_code, 403)
        self.assertFalse(StorageHold.objects.exists())

    def test_listing_donated_to_the_hub_can_be_held(self) -> None:
        listing = make_listing(self.owner, donation_hub=self.hub)
        self.assertEqual(self._hold(listing).status_code, 201)

    def test_listing_whose_scan_draft_is_routed_to_the_hub_can_be_held(self) -> None:
        listing = make_listing(self.owner)
        session = RoomScanSession.objects.create(owner=self.owner, name="Room", room_label="4B")
        RoomScanItemDraft.objects.create(
            scan_session=session, title="Lamp", linked_listing=listing, donation_hub=self.hub
        )
        self.assertEqual(self._hold(listing).status_code, 201)

    def test_staff_can_still_hold_any_listing(self) -> None:
        self.client.force_authenticate(make_user("staff@campus.test", is_staff=True))
        self.assertEqual(self._hold(make_listing(self.owner)).status_code, 201)


class BulkPublishLimitsTests(AuditTestCase):
    """Bulk publish from a scan had no rate limit, unlike creating one listing."""

    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner@campus.test")
        self.session = RoomScanSession.objects.create(owner=self.owner, name="Room", room_label="4B")
        self.client.force_authenticate(self.owner)

    def test_oversized_batches_are_refused(self) -> None:
        for name in ("scan-sessions-publish-selected", "scan-sessions-bulk-convert"):
            with self.subTest(endpoint=name):
                response = self.client.post(
                    reverse(name, args=[self.session.pk]),
                    {"item_ids": list(range(1, 102)), "action": "mark_keep"},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("item_ids", response.data)

    def test_requests_are_rate_limited(self) -> None:
        url = reverse("scan-sessions-publish-selected", args=[self.session.pk])
        statuses = {
            self.client.post(url, {"item_ids": [999_999]}, format="json").status_code for _ in range(60)
        }
        self.assertNotIn(429, statuses)
        self.assertEqual(self.client.post(url, {"item_ids": [999_999]}, format="json").status_code, 429)


class ReportTargetsTheUrlListingTests(AuditTestCase):
    """A report was validated against the listing named in the body but saved on the one in the URL.

    Naming another listing in the body skipped the own-listing and duplicate
    checks, and a repeat report then hit the unique constraint as a 500.
    """

    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner@campus.test")
        self.reporter = make_user("reporter@campus.test")
        self.listing = make_listing(self.owner)
        self.decoy = make_listing(make_user("someone@campus.test"), title="Decoy")

    def _report(self, user: Any, **body: Any) -> Any:
        self.client.force_authenticate(user)
        payload = {"reason": ListingReport.Reason.SPAM, "details": "Looks fake.", **body}
        return self.client.post(reverse("listings-reports", args=[self.listing.pk]), payload, format="json")

    def test_body_listing_is_ignored(self) -> None:
        response = self._report(self.reporter, listing=self.decoy.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ListingReport.objects.get().listing_id, self.listing.pk)

    def test_owner_cannot_report_their_own_listing_by_naming_another(self) -> None:
        response = self._report(self.owner, listing=self.decoy.pk)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ListingReport.objects.exists())

    def test_second_report_is_a_400_whatever_the_body_says(self) -> None:
        self.assertEqual(self._report(self.reporter).status_code, 201)
        self.assertEqual(self._report(self.reporter, listing=self.decoy.pk).status_code, 400)
        self.assertEqual(ListingReport.objects.count(), 1)


class MalformedScanDraftTests(AuditTestCase):
    """A hotspot that is not an object crashed the draft endpoint instead of being refused."""

    def test_non_object_hotspot_is_a_400(self) -> None:
        owner = make_user("owner@campus.test")
        session = RoomScanSession.objects.create(owner=owner, name="Room", room_label="4B")
        self.client.force_authenticate(owner)
        for hotspot in ("left corner", [0.1, 0.2, 0.3, 0.4], 7):
            with self.subTest(hotspot=hotspot):
                response = self.client.post(
                    reverse("scan-sessions-items", args=[session.pk]),
                    {"title": "Lamp", "hotspot_box": hotspot},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)


class InteractionSignalsTests(AuditTestCase):
    """Saves and reservations feed recommendations and the forecast, but only views were recorded."""

    def setUp(self) -> None:
        super().setUp()
        self.user = make_user("shopper@campus.test")
        self.listing = make_listing(make_user("owner@campus.test"))
        self.client.force_authenticate(self.user)

    def _actions(self) -> list[str]:
        from .models import ListingInteractionEvent

        return sorted(
            ListingInteractionEvent.objects.filter(user=self.user, listing=self.listing).values_list(
                "action", flat=True
            )
        )

    def test_view_save_and_reserve_are_each_recorded_once_a_day(self) -> None:
        detail = reverse("listings-detail", args=[self.listing.pk])
        save = reverse("listings-save", args=[self.listing.pk])
        self.client.get(detail)
        self.client.get(detail)
        self.assertEqual(self.client.post(save).status_code, 201)
        self.client.delete(save)
        self.client.post(save)  # saving again the same day adds nothing
        reserved = self.client.post(
            reverse("reservations-list"),
            {"listing": self.listing.pk, "pickup_time_window": "Friday 3pm"},
            format="json",
        )
        self.assertEqual(reserved.status_code, 201)

        self.assertEqual(self._actions(), ["reserve", "save", "view"])


class EditsDoNotOverwriteConcurrentChangesTests(AuditTestCase):
    """Saving an edit wrote the whole row back, including columns somebody else had just changed."""

    def setUp(self) -> None:
        super().setUp()
        self.owner = make_user("owner@campus.test")
        self.listing = make_listing(self.owner)

    def test_listing_edit_keeps_a_status_that_changed_during_the_request(self) -> None:
        from .serializers import ListingSerializer

        stale_copy = Listing.objects.get(pk=self.listing.pk)
        # A claim lands while the owner's edit is still in flight.
        Listing.objects.filter(pk=self.listing.pk).update(status=Listing.Status.RESERVED)

        serializer = ListingSerializer(stale_copy, data={"title": "Desk lamp (LED)"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, "Desk lamp (LED)")
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)

    def test_reservation_note_edit_keeps_a_completion_that_happened_meanwhile(self) -> None:
        claimant = make_user("claimant@campus.test")
        reservation = Reservation.objects.create(
            listing=self.listing,
            claimant=claimant,
            status=Reservation.Status.CONFIRMED,
            handoff_pin="123456",
            pickup_time_window="Fri 3-5pm",
        )
        self.client.force_authenticate(claimant)
        url = reverse("reservations-detail", args=[reservation.pk])

        from .serializers import ReservationSerializer

        stale_copy = Reservation.objects.get(pk=reservation.pk)
        Reservation.objects.filter(pk=reservation.pk).update(
            status=Reservation.Status.COMPLETED, handoff_pin=""
        )
        request = self.client.get(url).wsgi_request
        serializer = ReservationSerializer(
            stale_copy, data={"pickup_time_window": "Sat 10am"}, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        reservation.refresh_from_db()
        self.assertEqual(reservation.pickup_time_window, "Sat 10am")
        self.assertEqual(reservation.status, Reservation.Status.COMPLETED)
        self.assertEqual(reservation.handoff_pin, "")


class MatchedRequestFollowsItsSeekerTests(AuditTestCase):
    """A request matched to a listing was closed or reopened by whoever reserved that listing."""

    def setUp(self) -> None:
        super().setUp()
        from .models import RescueRequest

        self.owner = make_user("owner@campus.test")
        self.seeker = make_user("seeker@campus.test")
        self.stranger = make_user("stranger@campus.test")
        self.listing = make_listing(self.owner)
        self.request_row = RescueRequest.objects.create(
            seeker=self.seeker,
            title="Need a lamp",
            category=Listing.Category.LIGHTING,
            pickup_zone="Library",
            needed_by=timezone.now() + timedelta(days=2),
            matched_listing=self.listing,
            status=RescueRequest.Status.MATCHED,
        )

    def _confirmed(self, claimant: Any) -> Reservation:
        from .services import confirm_reservation

        reservation = Reservation.objects.create(
            listing=self.listing, claimant=claimant, pickup_time_window="Fri 3-5pm"
        )
        return confirm_reservation(reservation, notify=False)

    def _status(self) -> tuple[str, int | None]:
        self.request_row.refresh_from_db()
        return self.request_row.status, self.request_row.matched_listing_id

    def test_pickup_by_the_seeker_fulfils_the_request(self) -> None:
        from .services import complete_reservation

        complete_reservation(self._confirmed(self.seeker), notify=False)
        self.assertEqual(self._status(), ("fulfilled", self.listing.pk))

    def test_pickup_by_someone_else_reopens_the_request(self) -> None:
        from .services import complete_reservation

        complete_reservation(self._confirmed(self.stranger), notify=False)
        self.assertEqual(self._status(), ("open", None))

    def test_a_cancellation_by_someone_else_leaves_the_match_alone(self) -> None:
        from .services import cancel_reservation

        cancel_reservation(self._confirmed(self.stranger), actor=self.stranger)
        self.assertEqual(self._status(), ("matched", self.listing.pk))

    def test_a_cancellation_by_the_seeker_reopens_the_request(self) -> None:
        from .services import cancel_reservation

        cancel_reservation(self._confirmed(self.seeker), actor=self.seeker)
        self.assertEqual(self._status(), ("open", None))
