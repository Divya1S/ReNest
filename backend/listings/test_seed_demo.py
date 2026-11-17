"""`manage.py seed_demo` is the documented first step for a new checkout.

These tests pin what the README promises: the two demo accounts can sign in
and use every part of the handoff flow without any extra setup, and running
the command again is safe.
"""

from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Listing, RescueRequest, Reservation, RoomScanSession
from .services import cancel_reservation

User = get_user_model()


def seed() -> None:
    call_command("seed_demo", stdout=StringIO())


def client_for(email: str) -> APIClient:
    client = APIClient()
    client.force_authenticate(User.objects.get(email=email))
    return client


class SeedDemoCommandTests(TestCase):
    def test_demo_accounts_are_verified_and_share_a_campus(self) -> None:
        seed()
        maya = User.objects.get(email="maya@renest.local")
        leo = User.objects.get(email="leo@renest.local")

        for user in (maya, leo):
            self.assertTrue(user.check_password("demo-pass-123"))
            # Posting a listing requires a verified address.
            self.assertTrue(user.email_verified)
        self.assertIsNotNone(maya.campus)
        self.assertEqual(maya.campus, leo.campus)

    def test_seeded_content_is_visible_in_the_app(self) -> None:
        seed()
        browse = APIClient().get(reverse("listings-list"))
        titles = {row["title"] for row in browse.data["results"]}
        self.assertIn("Stackable storage bins", titles)  # Maya's
        self.assertIn("Gooseneck desk lamp", titles)  # Leo's

        maya = client_for("maya@renest.local")
        scan = RoomScanSession.objects.get(owner__email="maya@renest.local")
        self.assertEqual(maya.get(reverse("scan-sessions-detail", args=[scan.pk])).status_code, 200)
        self.assertEqual(maya.get(reverse("scan-sessions-board", args=[scan.pk])).status_code, 200)
        self.assertEqual(maya.get(reverse("leaderboard")).status_code, 200)
        self.assertEqual(maya.get(reverse("dashboard")).status_code, 200)
        # The move-out plan has its checklist, as a scan started in the app would.
        self.assertTrue(scan.tasks.filter(is_system=True).exists())

    def test_seeded_handoff_completes_with_its_pin(self) -> None:
        seed()
        reservation = Reservation.objects.get(status=Reservation.Status.CONFIRMED)
        self.assertRegex(reservation.handoff_pin, r"^\d{6}$")

        response = client_for("maya@renest.local").post(
            reverse("reservation-verify-pin", args=[reservation.pk]),
            {"pin": reservation.handoff_pin},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.COMPLETED)

    def test_running_twice_creates_nothing_new(self) -> None:
        seed()
        counts = (
            User.objects.count(),
            Listing.objects.count(),
            Reservation.objects.count(),
            RescueRequest.objects.count(),
            RoomScanSession.objects.count(),
        )
        pin = Reservation.objects.get(status=Reservation.Status.CONFIRMED).handoff_pin

        seed()

        self.assertEqual(
            counts,
            (
                User.objects.count(),
                Listing.objects.count(),
                Reservation.objects.count(),
                RescueRequest.objects.count(),
                RoomScanSession.objects.count(),
            ),
        )
        self.assertEqual(Reservation.objects.get(status=Reservation.Status.CONFIRMED).handoff_pin, pin)

    def test_rerun_after_a_demo_user_cancelled_and_reserved_again(self) -> None:
        seed()
        leo = User.objects.get(email="leo@renest.local")
        first = Reservation.objects.get(status=Reservation.Status.CONFIRMED)
        cancel_reservation(first, actor=leo)
        Reservation.objects.create(
            listing=first.listing, claimant=leo, pickup_time_window="Friday after class"
        )

        seed()

        live = Reservation.objects.filter(
            listing=first.listing, status__in=Reservation.ACTIVE_STATUSES
        )
        self.assertEqual(live.count(), 1)
        self.assertEqual(live.get().status, Reservation.Status.CONFIRMED)
        self.assertRegex(live.get().handoff_pin, r"^\d{6}$")
