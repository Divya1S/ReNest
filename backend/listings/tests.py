from datetime import timedelta
from decimal import Decimal
from io import BytesIO, StringIO
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from hubs.models import DonationHub

from .models import (
    HandoffFeedback,
    Listing,
    ListingReport,
    ListingUpdate,
    MoveOutTask,
    Notification,
    Reservation,
    ReservationMessage,
    RescueRequest,
    RoomScanImage,
    RoomScanItemDraft,
    RoomScanSession,
    SavedListing,
)
from .transactional_emails import (
    send_handoff_reminder_emails,
    send_reservation_completed_email,
    send_reservation_confirmed_email,
)

User = get_user_model()


def jpeg_upload(name="scan.jpg"):
    buf = BytesIO()
    Image.new("RGB", (40, 40), color=(255, 143, 90)).save(buf, format="JPEG")
    buf.seek(0)
    return InMemoryUploadedFile(buf, "image", name, "image/jpeg", buf.getbuffer().nbytes, None)


class ListingModelTests(APITestCase):
    def test_refresh_status_marks_past_due_listing_expired(self):
        owner = User.objects.create_user(
            email="owner@example.com",
            password="secretpass123",
            display_name="Owner",
        )
        listing = Listing.objects.create(
            owner=owner,
            title="Desk lamp",
            description="Still works great.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="Maple Hall lobby",
            available_until=timezone.now() - timedelta(hours=2),
        )

        listing.refresh_status()
        listing.refresh_from_db()
        self.assertEqual(listing.status, Listing.Status.EXPIRED)


class ListingApiTests(APITestCase):
    def setUp(self):
        # Dashboard/insights payloads are cached per user pk; SQLite reuses pks
        # across tests, so clear to avoid serving another test's cached payload.
        from django.core.cache import cache
        cache.clear()
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="secretpass123",
            display_name="Owner",
            campus_name="Pacific State",
            email_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="roomie@example.com",
            password="secretpass123",
            display_name="Roomie",
            campus_name="Pacific State",
            email_verified=True,
        )
        self.hub = DonationHub.objects.create(
            name="Campus Reuse Closet",
            campus_name="Pacific State",
            zone_label="Student Union East",
            description="Staffed donation zone.",
            open_instructions="Drop clean items between 4pm and 8pm.",
            active=True,
        )
        self.listing = Listing.objects.create(
            owner=self.owner,
            title="Storage cubes",
            description="Three stackable bins for under-bed storage.",
            category=Listing.Category.STORAGE,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount=Decimal("12.00"),
            estimated_retail_value=Decimal("28.00"),
            pickup_zone="Pine Hall front desk",
            available_until=timezone.now() + timedelta(days=2),
        )

    def create_scan_session(self):
        self.client.force_authenticate(self.owner)
        session_response = self.client.post(
            reverse("scan-sessions-list"),
            {
                "name": "North Hall sweep",
                "room_label": "North Hall 402",
                "room_type": RoomScanSession.RoomType.DORM_ROOM,
                "pickup_zone": "North Hall lobby",
                "move_out_deadline": (timezone.now() + timedelta(days=2)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(session_response.status_code, status.HTTP_201_CREATED)

        upload_response = self.client.post(
            reverse("scan-sessions-images", kwargs={"pk": session_response.data["id"]}),
            {"image": jpeg_upload("room-1.svg")},
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_201_CREATED)
        return upload_response.data

    def create_scan_draft(self, session_id, image_id, **overrides):
        payload = {
            "source_image": image_id,
            "hotspot_box": {"x": 0.12, "y": 0.18, "width": 0.24, "height": 0.2},
            **overrides,
        }
        response = self.client.post(
            reverse("scan-sessions-items", kwargs={"pk": session_id}),
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data

    def test_authenticated_user_can_create_listing(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("listings-list"),
            {
                "title": "Mini fan",
                "description": "Quiet, clean, and still in the box.",
                "category": Listing.Category.COMFORT,
                "condition": Listing.Condition.NEW,
                "price_type": Listing.PriceType.FREE,
                "estimated_retail_value": "20.00",
                "pickup_zone": "Elm Hall",
                "available_until": (timezone.now() + timedelta(days=1)).isoformat(),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Listing.objects.filter(owner=self.owner).count(), 2)

    def test_non_owner_cannot_edit_listing(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.patch(
            reverse("listings-detail", kwargs={"pk": self.listing.pk}),
            {"title": "Changed title"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reservation_flow_updates_listing_status(self):
        self.client.force_authenticate(self.other_user)
        create_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tonight between 7pm and 8pm",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        reservation = Reservation.objects.get(pk=create_response.data["id"])
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.RESERVED)
        self.assertEqual(reservation.status, Reservation.Status.REQUESTED)

        self.client.force_authenticate(self.owner)
        confirm_response = self.client.patch(
            reverse("reservations-detail", kwargs={"pk": reservation.pk}),
            {"status": Reservation.Status.CONFIRMED},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK)

        complete_response = self.client.patch(
            reverse("reservations-detail", kwargs={"pk": reservation.pk}),
            {"status": Reservation.Status.COMPLETED},
            format="json",
        )
        self.assertEqual(complete_response.status_code, status.HTTP_200_OK)

        reservation.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertEqual(reservation.status, Reservation.Status.COMPLETED)
        self.assertEqual(self.listing.status, Listing.Status.PICKED_UP)

    def test_reservation_detail_includes_handoff_fields(self):
        self.client.force_authenticate(self.other_user)
        create_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tomorrow between 5pm and 6pm",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        detail_response = self.client.get(
            reverse("reservations-detail", kwargs={"pk": create_response.data["id"]})
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertIn("handoff_code", detail_response.data)
        self.assertIn("next_step", detail_response.data)
        self.assertGreaterEqual(len(detail_response.data["handoff_checklist"]), 1)

    def test_completed_handoff_accepts_feedback_from_participants(self):
        self.client.force_authenticate(self.other_user)
        create_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tomorrow between 5pm and 6pm",
            },
            format="json",
        )
        reservation_id = create_response.data["id"]

        self.client.force_authenticate(self.owner)
        self.client.patch(
            reverse("reservations-detail", kwargs={"pk": reservation_id}),
            {"status": Reservation.Status.CONFIRMED},
            format="json",
        )
        self.client.patch(
            reverse("reservations-detail", kwargs={"pk": reservation_id}),
            {"status": Reservation.Status.COMPLETED},
            format="json",
        )

        feedback_response = self.client.post(
            reverse("reservations-feedback", kwargs={"pk": reservation_id}),
            {
                "rating": 5,
                "tags": ["responsive", "easy_pickup"],
                "note": "Clear communication and a smooth pickup.",
            },
            format="json",
        )
        self.assertEqual(feedback_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(HandoffFeedback.objects.filter(reservation_id=reservation_id).count(), 1)

        detail_response = self.client.get(
            reverse("reservations-detail", kwargs={"pk": reservation_id})
        )
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertFalse(detail_response.data["can_leave_feedback"])
        self.assertEqual(detail_response.data["feedback_summary"]["count"], 1)

    def test_feedback_is_blocked_before_handoff_completion(self):
        self.client.force_authenticate(self.other_user)
        create_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tonight between 7pm and 8pm",
            },
            format="json",
        )

        feedback_response = self.client.post(
            reverse("reservations-feedback", kwargs={"pk": create_response.data["id"]}),
            {
                "rating": 4,
                "tags": ["friendly"],
                "note": "Too early.",
            },
            format="json",
        )
        self.assertEqual(feedback_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rescue_request_can_be_created_and_matched(self):
        self.client.force_authenticate(self.other_user)
        create_response = self.client.post(
            reverse("requests-list"),
            {
                "title": "Need storage cubes",
                "description": "Looking for a small lamp before finals week.",
                "category": self.listing.category,
                "pickup_zone": "Library east entrance",
                "needed_by": (timezone.now() + timedelta(days=1)).isoformat(),
                "budget_amount": "15.00",
                "urgency": RescueRequest.Urgency.SOON,
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        request_id = create_response.data["id"]

        self.client.force_authenticate(self.owner)
        match_response = self.client.post(
            reverse("requests-match", kwargs={"pk": request_id}),
            {"listing": self.listing.pk},
            format="json",
        )
        self.assertEqual(match_response.status_code, status.HTTP_200_OK)
        self.assertEqual(match_response.data["status"], RescueRequest.Status.MATCHED)
        self.assertEqual(match_response.data["matched_listing"], self.listing.pk)

    def test_completed_reservation_fulfills_matched_request(self):
        rescue_request = RescueRequest.objects.create(
            seeker=self.other_user,
            title="Need storage cubes",
            description="For move-in next week.",
            category=self.listing.category,
            pickup_zone="Pine Hall front desk",
            needed_by=timezone.now() + timedelta(days=2),
            budget_amount=Decimal("20.00"),
            urgency=RescueRequest.Urgency.SOON,
            status=RescueRequest.Status.MATCHED,
            matched_listing=self.listing,
        )

        self.client.force_authenticate(self.other_user)
        create_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tomorrow between 5pm and 6pm",
            },
            format="json",
        )
        reservation_id = create_response.data["id"]

        self.client.force_authenticate(self.owner)
        self.client.patch(
            reverse("reservations-detail", kwargs={"pk": reservation_id}),
            {"status": Reservation.Status.CONFIRMED},
            format="json",
        )
        self.client.patch(
            reverse("reservations-detail", kwargs={"pk": reservation_id}),
            {"status": Reservation.Status.COMPLETED},
            format="json",
        )

        rescue_request.refresh_from_db()
        self.assertEqual(rescue_request.status, RescueRequest.Status.FULFILLED)

    def test_match_center_surfaces_supply_and_demand(self):
        RescueRequest.objects.create(
            seeker=self.other_user,
            title="Need storage cubes",
            description="For move-in next week.",
            category=self.listing.category,
            pickup_zone="Pine Hall front desk",
            needed_by=timezone.now() + timedelta(days=2),
            budget_amount=Decimal("20.00"),
            urgency=RescueRequest.Urgency.SOON,
        )
        RescueRequest.objects.create(
            seeker=self.owner,
            title="Need a desk lamp",
            description="For late-night study sessions.",
            category=Listing.Category.LIGHTING,
            pickup_zone="Pine Hall front desk",
            needed_by=timezone.now() + timedelta(days=2),
            budget_amount=Decimal("15.00"),
            urgency=RescueRequest.Urgency.SOON,
        )
        lamp_owner = User.objects.create_user(
            email="lamp-owner@example.com",
            password="secretpass123",
            display_name="Lamp Owner",
            campus_name="Pacific State",
        )
        Listing.objects.create(
            owner=lamp_owner,
            title="Study lamp",
            description="Warm light.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount=Decimal("12.00"),
            estimated_retail_value=Decimal("25.00"),
            pickup_zone="Maple Hall lobby",
            available_until=timezone.now() + timedelta(days=3),
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("match-center"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["stats"]["fulfillable_needs"], 1)
        self.assertEqual(response.data["stats"]["requests_with_matches"], 1)
        self.assertEqual(len(response.data["fulfill_opportunities"]), 1)
        self.assertEqual(len(response.data["request_recommendations"]), 1)

    def test_insights_endpoint_surfaces_campus_and_ops_metrics(self):
        self.listing.status = Listing.Status.PICKED_UP
        self.listing.save(update_fields=["status", "updated_at"])

        RescueRequest.objects.create(
            seeker=self.owner,
            title="Need a desk lamp",
            description="For late-night study sessions.",
            category=Listing.Category.LIGHTING,
            pickup_zone="Pine Hall front desk",
            needed_by=timezone.now() + timedelta(days=2),
            budget_amount=Decimal("15.00"),
            urgency=RescueRequest.Urgency.SOON,
            status=RescueRequest.Status.OPEN,
        )
        matched_request = RescueRequest.objects.create(
            seeker=self.other_user,
            title="Need storage cubes",
            description="For move-in next week.",
            category=Listing.Category.STORAGE,
            pickup_zone="Pine Hall front desk",
            needed_by=timezone.now() + timedelta(days=2),
            budget_amount=Decimal("20.00"),
            urgency=RescueRequest.Urgency.URGENT,
            status=RescueRequest.Status.MATCHED,
            matched_listing=self.listing,
        )

        second_listing = Listing.objects.create(
            owner=self.owner,
            title="Clip lamp",
            description="Portable and bright.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="Maple Hall lobby",
            available_until=timezone.now() + timedelta(days=3),
            source_scan_session=None,
        )
        report = ListingReport.objects.create(
            listing=second_listing,
            reporter=self.other_user,
            reason=ListingReport.Reason.INACCURATE,
            details="Pickup zone looked outdated.",
            status=ListingReport.Status.OPEN,
        )

        reservation = Reservation.objects.create(
            listing=second_listing,
            claimant=self.other_user,
            status=Reservation.Status.COMPLETED,
            pickup_time_window="Tomorrow afternoon",
        )
        HandoffFeedback.objects.create(
            reservation=reservation,
            reviewer=self.other_user,
            reviewee=self.owner,
            rating=5,
            tags=["responsive"],
            note="Smooth handoff.",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("insights"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["my_impact"]["rescued_items"], 1)
        self.assertEqual(response.data["campus_snapshot"]["open_reports"], 1)
        self.assertGreaterEqual(response.data["campus_snapshot"]["open_requests"], 1)
        self.assertGreaterEqual(len(response.data["weekly_activity"]), 1)
        self.assertGreaterEqual(len(response.data["category_market_map"]), 1)
        self.assertGreaterEqual(len(response.data["recommendations"]), 1)
        self.assertIn("requests_you_can_fulfill", response.data["ops_snapshot"])
        self.assertEqual(report.status, ListingReport.Status.OPEN)
        self.assertEqual(matched_request.status, RescueRequest.Status.MATCHED)

    def test_saved_listing_flow_and_dashboard(self):
        self.client.force_authenticate(self.other_user)
        save_response = self.client.post(
            reverse("listings-save", kwargs={"pk": self.listing.pk}),
            {},
            format="json",
        )
        self.assertEqual(save_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SavedListing.objects.filter(user=self.other_user).count(), 1)

        list_response = self.client.get(reverse("saved-listings"))
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["count"], 1)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, status.HTTP_200_OK)
        self.assertEqual(dashboard_response.data["stats"]["saved_items"], 1)

        unsave_response = self.client.delete(reverse("listings-save", kwargs={"pk": self.listing.pk}))
        self.assertEqual(unsave_response.status_code, status.HTTP_200_OK)
        self.assertEqual(SavedListing.objects.filter(user=self.other_user).count(), 0)

    def test_listing_updates_allow_claimant_and_owner(self):
        self.client.force_authenticate(self.other_user)
        reservation_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tomorrow after class",
            },
            format="json",
        )
        self.assertEqual(reservation_response.status_code, status.HTTP_201_CREATED)

        update_response = self.client.post(
            reverse("listings-updates", kwargs={"pk": self.listing.pk}),
            {"body": "I can grab this after my 4pm lab."},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ListingUpdate.objects.filter(listing=self.listing).count(), 1)

        self.client.force_authenticate(self.owner)
        owner_update = self.client.post(
            reverse("listings-updates", kwargs={"pk": self.listing.pk}),
            {"body": "Perfect. I will meet you in the lobby."},
            format="json",
        )
        self.assertEqual(owner_update.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ListingUpdate.objects.filter(listing=self.listing).count(), 2)

    def test_scan_session_upload_and_hotspot_draft_creation(self):
        session_data = self.create_scan_session()
        draft_response = self.create_scan_draft(session_data["id"], session_data["images"][0]["id"])

        self.assertTrue(draft_response["title"])
        self.assertEqual(RoomScanSession.objects.count(), 1)
        self.assertEqual(RoomScanItemDraft.objects.count(), 1)
        self.assertGreater(MoveOutTask.objects.filter(scan_session_id=session_data["id"]).count(), 0)
        self.assertEqual(RoomScanSession.objects.get().status, RoomScanSession.Status.CLEAROUT)

    def test_move_out_plan_endpoint_and_custom_task_flow(self):
        session_data = self.create_scan_session()

        plan_response = self.client.get(
            reverse("scan-sessions-plan", kwargs={"pk": session_data["id"]})
        )
        self.assertEqual(plan_response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(plan_response.data["task_summary"]["total_tasks"], 1)

        create_task_response = self.client.post(
            reverse("scan-sessions-tasks", kwargs={"pk": session_data["id"]}),
            {
                "title": "Return mini fridge dolly",
                "details": "Bring the checkout dolly back to the housing desk.",
                "category": MoveOutTask.Category.ADMIN,
                "due_at": (timezone.now() + timedelta(hours=6)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(create_task_response.status_code, status.HTTP_201_CREATED)

        task_id = create_task_response.data["id"]
        update_task_response = self.client.patch(
            reverse("scan-tasks-detail", kwargs={"pk": task_id}),
            {"status": MoveOutTask.Status.DONE},
            format="json",
        )
        self.assertEqual(update_task_response.status_code, status.HTTP_200_OK)

        created_task = MoveOutTask.objects.get(pk=task_id)
        self.assertEqual(created_task.status, MoveOutTask.Status.DONE)
        self.assertIsNotNone(created_task.completed_at)

    def test_scan_bulk_convert_creates_listing_and_links_draft(self):
        session_data = self.create_scan_session()
        draft_response = self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Mini fan rescue",
            category=Listing.Category.COMFORT,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            price_amount="0.00",
            estimated_retail_value="24.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )

        response = self.client.post(
            reverse("scan-sessions-bulk-convert", kwargs={"pk": session_data["id"]}),
            {
                "item_ids": [draft_response["id"]],
                "action": "create_free_listings",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        draft = RoomScanItemDraft.objects.get(pk=draft_response["id"])
        assert draft.linked_listing is not None
        self.assertEqual(draft.linked_listing.source_scan_session_id, session_data["id"])
        self.assertEqual(draft.triage_status, RoomScanItemDraft.TriageStatus.DONE)

    def test_scan_bulk_donation_routes_without_creating_listing(self):
        session_data = self.create_scan_session()
        draft_response = self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Toiletry tote",
            category=Listing.Category.TOILETRIES,
            condition=Listing.Condition.NEW,
            price_type=Listing.PriceType.FREE,
            price_amount="0.00",
            estimated_retail_value="14.00",
            triage_status=RoomScanItemDraft.TriageStatus.DONATE,
        )

        response = self.client.post(
            reverse("scan-sessions-bulk-convert", kwargs={"pk": session_data["id"]}),
            {
                "item_ids": [draft_response["id"]],
                "action": "send_to_donation_hub",
                "donation_hub": self.hub.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        draft = RoomScanItemDraft.objects.get(pk=draft_response["id"])
        self.assertIsNone(draft.linked_listing)
        self.assertEqual(draft.donation_hub_id, self.hub.pk)
        self.assertEqual(draft.triage_status, RoomScanItemDraft.TriageStatus.DONE)

    def test_publish_presets_endpoint_returns_curated_presets(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("publish-presets"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        keys = [preset["key"] for preset in response.data]
        self.assertIn("desk_lamp", keys)
        self.assertIn("storage_bin", keys)

    def test_publish_queue_reports_ready_and_missing_info_states(self):
        session_data = self.create_scan_session()
        ready_draft = self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Ready lamp",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount="9.00",
            estimated_retail_value="22.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )
        blocked_draft = self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Need price bin",
            category=Listing.Category.STORAGE,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount="0.00",
            estimated_retail_value="18.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )

        response = self.client.get(
            reverse("scan-sessions-publish-queue", kwargs={"pk": session_data["id"]})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        items = {item["id"]: item for item in response.data["items"]}
        self.assertEqual(items[ready_draft["id"]]["publish_readiness"], "ready")
        self.assertEqual(items[ready_draft["id"]]["missing_fields"], [])
        self.assertEqual(items[blocked_draft["id"]]["publish_readiness"], "needs_info")
        self.assertIn("price_amount", items[blocked_draft["id"]]["missing_fields"])

    def test_batch_update_only_changes_selected_drafts(self):
        session_data = self.create_scan_session()
        first_draft = self.create_scan_draft(session_data["id"], session_data["images"][0]["id"])
        second_draft = self.create_scan_draft(session_data["id"], session_data["images"][0]["id"])

        response = self.client.patch(
            reverse("scan-sessions-batch-update", kwargs={"pk": session_data["id"]}),
            {
                "item_ids": [first_draft["id"]],
                "changes": {
                    "preset_key": "desk_lamp",
                    "triage_status": RoomScanItemDraft.TriageStatus.SELL,
                    "pickup_zone": "Updated pickup desk",
                },
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        updated_draft = RoomScanItemDraft.objects.get(pk=first_draft["id"])
        untouched_draft = RoomScanItemDraft.objects.get(pk=second_draft["id"])
        session = RoomScanSession.objects.get(pk=session_data["id"])

        self.assertEqual(updated_draft.preset_key, "desk_lamp")
        self.assertEqual(updated_draft.title, "Desk lamp")
        self.assertEqual(updated_draft.triage_status, RoomScanItemDraft.TriageStatus.SELL)
        self.assertNotEqual(untouched_draft.preset_key, "desk_lamp")
        self.assertEqual(session.pickup_zone, "Updated pickup desk")

    def test_publish_selected_blocks_incomplete_drafts(self):
        session_data = self.create_scan_session()
        draft_response = self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Blocked fan",
            category=Listing.Category.COMFORT,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount="0.00",
            estimated_retail_value="24.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )

        response = self.client.post(
            reverse("scan-sessions-publish-selected", kwargs={"pk": session_data["id"]}),
            {"item_ids": [draft_response["id"]]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["blocked_items"][0]["id"], draft_response["id"])
        self.assertIn("price_amount", response.data["blocked_items"][0]["missing_fields"])

    def test_publish_selected_creates_live_listings_for_ready_drafts(self):
        session_data = self.create_scan_session()
        draft_response = self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Ready fan",
            category=Listing.Category.COMFORT,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount="11.00",
            estimated_retail_value="30.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )

        response = self.client.post(
            reverse("scan-sessions-publish-selected", kwargs={"pk": session_data["id"]}),
            {"item_ids": [draft_response["id"]]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["published_count"], 1)
        draft = RoomScanItemDraft.objects.get(pk=draft_response["id"])
        assert draft.linked_listing is not None
        self.assertEqual(draft.linked_listing.title, "Ready fan")
        self.assertEqual(draft.triage_status, RoomScanItemDraft.TriageStatus.DONE)

    def test_dashboard_includes_scan_stats(self):
        session_data = self.create_scan_session()
        self.create_scan_draft(session_data["id"], session_data["images"][0]["id"])

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("active_scans", response.data["stats"])
        self.assertIn("scan_overview", response.data)
        self.assertEqual(len(response.data["scan_overview"]), 1)

    def test_dashboard_includes_level_four_action_queues(self):
        session_data = self.create_scan_session()
        self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Publish-ready lamp",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount="8.00",
            estimated_retail_value="22.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )
        self.create_scan_draft(
            session_data["id"],
            session_data["images"][0]["id"],
            title="Blocked storage bin",
            category=Listing.Category.STORAGE,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount="0.00",
            estimated_retail_value="18.00",
            triage_status=RoomScanItemDraft.TriageStatus.SELL,
        )
        self.client.force_authenticate(self.other_user)
        reservation_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tomorrow 3pm to 4pm",
            },
            format="json",
        )
        self.assertEqual(reservation_response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("action_queues", response.data)
        self.assertGreaterEqual(response.data["stats"]["ready_to_publish_now"], 1)
        self.assertGreaterEqual(response.data["stats"]["blocked_scans"], 1)
        self.assertGreaterEqual(response.data["stats"]["incoming_pickup_actions"], 1)
        self.assertGreaterEqual(len(response.data["action_queues"]["ready_to_publish_now"]), 1)
        self.assertGreaterEqual(len(response.data["action_queues"]["scans_blocked_by_missing_info"]), 1)
        self.assertGreaterEqual(len(response.data["action_queues"]["incoming_pickup_actions"]), 1)

    def test_dashboard_includes_move_out_task_actions(self):
        session_data = self.create_scan_session()
        overdue_task = MoveOutTask.objects.filter(scan_session_id=session_data["id"]).first()
        assert overdue_task is not None
        overdue_task.due_at = timezone.now() - timedelta(hours=2)
        overdue_task.status = MoveOutTask.Status.TODO
        overdue_task.save(update_fields=["due_at", "status", "updated_at"])

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("tasks_due_today", response.data["stats"])
        self.assertIn("overdue_tasks", response.data["stats"])
        self.assertIn("move_out_tasks", response.data["action_queues"])
        self.assertGreaterEqual(response.data["stats"]["overdue_tasks"], 1)
        self.assertGreaterEqual(len(response.data["action_queues"]["move_out_tasks"]), 1)


    def test_listing_report_flow_and_trust_overview(self):
        self.client.force_authenticate(self.other_user)
        report_response = self.client.post(
            reverse("listings-reports", kwargs={"pk": self.listing.pk}),
            {
                "listing": self.listing.pk,
                "reason": ListingReport.Reason.INACCURATE,
                "details": "The item description does not match the photos.",
            },
            format="json",
        )
        self.assertEqual(report_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ListingReport.objects.filter(listing=self.listing).count(), 1)

        duplicate_response = self.client.post(
            reverse("listings-reports", kwargs={"pk": self.listing.pk}),
            {
                "listing": self.listing.pk,
                "reason": ListingReport.Reason.SPAM,
                "details": "Duplicate report should be blocked.",
            },
            format="json",
        )
        self.assertEqual(duplicate_response.status_code, status.HTTP_400_BAD_REQUEST)

        trust_response = self.client.get(reverse("trust-overview"))
        self.assertEqual(trust_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(trust_response.data["reports_filed"]), 1)
        self.assertIn("completed_rescues", trust_response.data["summary"])
        self.assertIn("total_feedback_received", trust_response.data["summary"])
        self.assertIn("received_feedback", trust_response.data)
        self.assertIn("pending_feedback", trust_response.data)

        listing_detail = self.client.get(reverse("listings-detail", kwargs={"pk": self.listing.pk}))
        self.assertEqual(listing_detail.status_code, status.HTTP_200_OK)
        self.assertTrue(listing_detail.data["has_reported"])
        self.assertFalse(listing_detail.data["can_report"])
        self.assertIn("owner_trust_summary", listing_detail.data)

    def test_owner_cannot_report_own_listing(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("listings-reports", kwargs={"pk": self.listing.pk}),
            {
                "listing": self.listing.pk,
                "reason": ListingReport.Reason.SAFETY,
                "details": "Should not be allowed.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LaunchReadinessTests(APITestCase):
    def setUp(self):
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        self.owner = User.objects.create_user(
            email="ops-owner@example.com",
            password="secretpass123",
            display_name="Ops Owner",
            campus_name="Pacific State",
            email_verified=True,
        )
        self.other_user = User.objects.create_user(
            email="ops-claimant@example.com",
            password="secretpass123",
            display_name="Ops Claimant",
            campus_name="Pacific State",
            email_verified=True,
        )
        self.listing = Listing.objects.create(
            owner=self.owner,
            title="Launch lamp",
            description="Ready for pickup.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.LOW_COST,
            price_amount=Decimal("8.00"),
            estimated_retail_value=Decimal("24.00"),
            pickup_zone="North Hall lobby",
            available_until=timezone.now() + timedelta(hours=8),
        )

    def create_ready_scan(self):
        self.client.force_authenticate(self.owner)
        session_response = self.client.post(
            reverse("scan-sessions-list"),
            {
                "name": "Ops Sweep",
                "room_label": "North Hall 402",
                "room_type": RoomScanSession.RoomType.DORM_ROOM,
                "pickup_zone": "North Hall lobby",
                "move_out_deadline": (timezone.now() + timedelta(hours=18)).isoformat(),
            },
            format="json",
        )
        upload_response = self.client.post(
            reverse("scan-sessions-images", kwargs={"pk": session_response.data["id"]}),
            {"image": jpeg_upload("ops-room.svg")},
            format="multipart",
        )
        draft_response = self.client.post(
            reverse("scan-sessions-items", kwargs={"pk": session_response.data["id"]}),
            {
                "source_image": upload_response.data["images"][0]["id"],
                "hotspot_box": {"x": 0.12, "y": 0.18, "width": 0.24, "height": 0.2},
                "title": "Ready desk lamp",
                "category": Listing.Category.LIGHTING,
                "condition": Listing.Condition.GOOD,
                "price_type": Listing.PriceType.LOW_COST,
                "price_amount": "9.00",
                "estimated_retail_value": "22.00",
                "triage_status": RoomScanItemDraft.TriageStatus.SELL,
            },
            format="json",
        )
        self.assertEqual(draft_response.status_code, status.HTTP_201_CREATED)
        return session_response.data["id"]

    def test_health_endpoints_are_available(self):
        live_response = self.client.get(reverse("health-live"))
        ready_response = self.client.get(reverse("health-ready"))

        self.assertEqual(live_response.status_code, status.HTTP_200_OK)
        self.assertEqual(ready_response.status_code, status.HTTP_200_OK)
        self.assertEqual(live_response.data["status"], "ok")
        self.assertEqual(ready_response.data["status"], "ready")
        self.assertIn("database", ready_response.data["checks"])
        self.assertIn("media_root", ready_response.data["checks"])

    def test_notifications_api_marks_items_read_and_dashboard_surfaces_preview(self):
        self.create_ready_scan()
        self.client.force_authenticate(self.other_user)
        reservation_response = self.client.post(
            reverse("reservations-list"),
            {
                "listing": self.listing.pk,
                "pickup_time_window": "Tonight between 6pm and 7pm",
            },
            format="json",
        )
        self.assertEqual(reservation_response.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(self.owner)
        notifications_response = self.client.get(reverse("notifications-list"))
        self.assertEqual(notifications_response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(notifications_response.data["unread_count"], 1)
        first_notification = notifications_response.data["results"][0]

        patch_response = self.client.patch(
            reverse("notifications-detail", kwargs={"pk": first_notification["id"]}),
            {"is_read": True},
            format="json",
        )
        self.assertEqual(patch_response.status_code, status.HTTP_200_OK)
        self.assertTrue(Notification.objects.get(pk=first_notification["id"]).is_read)

        read_all_response = self.client.post(reverse("notifications-read-all"), {}, format="json")
        self.assertEqual(read_all_response.status_code, status.HTTP_200_OK)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, status.HTTP_200_OK)
        self.assertIn("notification_preview", dashboard_response.data)
        self.assertIn("unread_notifications", dashboard_response.data["stats"])

    def test_generate_notifications_is_idempotent_and_run_maintenance_expires_stale_listing(self):
        self.create_ready_scan()
        stale_listing = Listing.objects.create(
            owner=self.owner,
            title="Old storage bin",
            description="Past deadline.",
            category=Listing.Category.STORAGE,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="Cedar Hall desk",
            available_until=timezone.now() - timedelta(hours=3),
        )

        output = StringIO()
        call_command("generate_notifications", stdout=output)
        first_count = Notification.objects.count()
        call_command("generate_notifications", stdout=output)
        second_count = Notification.objects.count()
        self.assertEqual(first_count, second_count)

        call_command("run_maintenance", stdout=output)
        stale_listing.refresh_from_db()
        self.assertEqual(stale_listing.status, Listing.Status.EXPIRED)

    def test_admin_changelists_load_for_staff(self):
        task_session_id = self.create_ready_scan()
        MoveOutTask.objects.filter(scan_session_id=task_session_id).first()
        Notification.objects.create(
            user=self.owner,
            type=Notification.Type.MOVE_OUT_TASK,
            title="Admin smoke",
            body="Admin check.",
            link_path="/dashboard",
            priority=Notification.Priority.NORMAL,
            dedupe_key="admin-smoke",
        )
        staff = User.objects.create_superuser(
            email="staff@example.com",
            password="secretpass123",
            display_name="Staff User",
        )
        self.client.force_login(staff)

        listing_admin = self.client.get(reverse("admin:listings_listing_changelist"))
        task_admin = self.client.get(reverse("admin:listings_moveouttask_changelist"))
        notification_admin = self.client.get(reverse("admin:listings_notification_changelist"))
        feedback_admin = self.client.get(reverse("admin:listings_handofffeedback_changelist"))
        request_admin = self.client.get(reverse("admin:listings_rescuerequest_changelist"))

        self.assertEqual(listing_admin.status_code, status.HTTP_200_OK)
        self.assertEqual(task_admin.status_code, status.HTTP_200_OK)
        self.assertEqual(notification_admin.status_code, status.HTTP_200_OK)
        self.assertEqual(feedback_admin.status_code, status.HTTP_200_OK)
        self.assertEqual(request_admin.status_code, status.HTTP_200_OK)


class TransactionalEmailTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@tx.test",
            password="pass",
            display_name="Owner",
            email_verified=True,
        )
        self.claimant = User.objects.create_user(
            email="claimant@tx.test",
            password="pass",
            display_name="Claimant",
            email_verified=True,
        )
        self.listing = Listing.objects.create(
            owner=self.owner,
            title="Blue Desk Lamp",
            description="Works fine.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="Lobby",
            available_until=timezone.now() + timedelta(hours=30),
        )
        self.reservation = Reservation.objects.create(
            listing=self.listing,
            claimant=self.claimant,
            status=Reservation.Status.REQUESTED,
            pickup_time_window="Tomorrow 2–4 pm",
        )

    def test_confirmed_email_sent_to_both_parties(self):
        from django.test import override_settings
        from django.core import mail

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            send_reservation_confirmed_email(self.reservation)

        self.assertEqual(len(mail.outbox), 2)
        recipients = {m.to[0] for m in mail.outbox}
        self.assertIn(self.claimant.email, recipients)
        self.assertIn(self.owner.email, recipients)
        claimant_mail = next(m for m in mail.outbox if m.to[0] == self.claimant.email)
        self.assertIn("confirmed", claimant_mail.subject.lower())
        self.assertIn(self.listing.title, claimant_mail.subject)

    def test_completed_email_requests_feedback_from_both(self):
        from django.test import override_settings
        from django.core import mail

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            send_reservation_completed_email(self.reservation)

        self.assertEqual(len(mail.outbox), 2)
        recipients = {m.to[0] for m in mail.outbox}
        self.assertIn(self.claimant.email, recipients)
        self.assertIn(self.owner.email, recipients)
        for m in mail.outbox:
            self.assertIn("feedback", m.subject.lower())

    def test_handoff_reminder_fires_once_per_reservation(self):
        from django.test import override_settings
        from django.core import mail

        # Move the deadline into the 24 ± 4 h window.
        self.listing.available_until = timezone.now() + timedelta(hours=24)
        self.listing.save(update_fields=["available_until"])

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            first_run = send_handoff_reminder_emails()
            second_run = send_handoff_reminder_emails()

        # First run sends; second run is deduped.
        self.assertEqual(first_run, 1)
        self.assertEqual(second_run, 0)
        self.assertEqual(len(mail.outbox), 2)  # one per party

    def test_handoff_reminder_skips_demo_listings(self):
        from django.test import override_settings
        from django.core import mail

        self.listing.is_demo = True
        self.listing.available_until = timezone.now() + timedelta(hours=24)
        self.listing.save(update_fields=["is_demo", "available_until"])

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            count = send_handoff_reminder_emails()

        self.assertEqual(count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_handoff_reminder_skips_out_of_window(self):
        from django.test import override_settings
        from django.core import mail

        # Deadline is 48 h away — outside the 24 ± 4 h window.
        self.listing.available_until = timezone.now() + timedelta(hours=48)
        self.listing.save(update_fields=["available_until"])

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            count = send_handoff_reminder_emails()

        self.assertEqual(count, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_reservation_api_sends_confirmed_email_on_status_change(self):
        from django.test import override_settings
        from django.core import mail

        self.listing.status = Listing.Status.RESERVED
        self.listing.save(update_fields=["status"])
        self.client.force_authenticate(self.owner)
        url = reverse("reservations-detail", kwargs={"pk": self.reservation.pk})

        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            response = self.client.patch(url, {"status": "confirmed"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 2)
        subjects = [m.subject for m in mail.outbox]
        self.assertTrue(any("confirmed" in s.lower() for s in subjects))


# ---------------------------------------------------------------------------
# Image compression edge cases
# ---------------------------------------------------------------------------

class ImageUtilsTests(APITestCase):
    def _make_upload(self, img, name="test.png"):
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return InMemoryUploadedFile(buf, "image", name, "image/png", buf.getbuffer().nbytes, None)

    def test_compress_image_rejects_oversized_file(self):
        from listings.image_utils import compress_image, MAX_UPLOAD_BYTES
        from rest_framework.exceptions import ValidationError

        img = Image.new("RGB", (100, 100), color=(255, 0, 0))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        upload = InMemoryUploadedFile(buf, "image", "big.png", "image/png", MAX_UPLOAD_BYTES + 1, None)

        with self.assertRaises(ValidationError) as ctx:
            compress_image(upload)
        self.assertIn("image", ctx.exception.detail)

    def test_compress_image_rejects_corrupt_file(self):
        from listings.image_utils import compress_image
        from rest_framework.exceptions import ValidationError

        bad = BytesIO(b"not-an-image-at-all")
        upload = InMemoryUploadedFile(bad, "image", "corrupt.jpg", "image/jpeg", bad.getbuffer().nbytes, None)

        with self.assertRaises(ValidationError) as ctx:
            compress_image(upload)
        self.assertIn("image", ctx.exception.detail)

    def test_compress_image_converts_rgba_to_rgb(self):
        from listings.image_utils import compress_image

        img = Image.new("RGBA", (50, 50), color=(0, 128, 255, 200))
        upload = self._make_upload(img, "rgba.png")
        result = compress_image(upload)
        self.assertEqual(result.content_type, "image/jpeg")

    def test_compress_image_resizes_large_image(self):
        from listings.image_utils import compress_image, MAX_DIMENSION

        img = Image.new("RGB", (MAX_DIMENSION + 500, MAX_DIMENSION + 500), color=(100, 200, 100))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        upload = InMemoryUploadedFile(buf, "image", "huge.png", "image/png", buf.getbuffer().nbytes, None)
        result = compress_image(upload)
        self.assertIsNotNone(result)
        self.assertEqual(result.content_type, "image/jpeg")


# ---------------------------------------------------------------------------
# Listing browse filter / sort / soft-delete filtering
# ---------------------------------------------------------------------------

class ListingBrowseFilterTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="filter@example.com",
            password="pass",
            display_name="Filter User",
            campus_name="Test U",
            email_verified=True,
        )
        self.client.force_authenticate(self.user)
        future = timezone.now() + timedelta(days=3)
        self.lamp = Listing.objects.create(
            owner=self.user, title="Desk Lamp", description="Study lamp",
            category=Listing.Category.LIGHTING, condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE, pickup_zone="Elm", available_until=future,
        )
        self.fan = Listing.objects.create(
            owner=self.user, title="Box Fan", description="Cooling fan",
            category=Listing.Category.COMFORT, condition=Listing.Condition.FAIR,
            price_type=Listing.PriceType.LOW_COST, price_amount="8.00",
            pickup_zone="Oak", available_until=future + timedelta(days=1),
        )

    def test_search_filters_by_title(self):
        response = self.client.get(reverse("listings-list"), {"search": "lamp"})
        self.assertEqual(response.status_code, 200)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.lamp.pk, ids)
        self.assertNotIn(self.fan.pk, ids)

    def test_category_filter(self):
        response = self.client.get(reverse("listings-list"), {"category": Listing.Category.COMFORT})
        self.assertEqual(response.status_code, 200)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.fan.pk, ids)
        self.assertNotIn(self.lamp.pk, ids)

    def test_price_type_filter(self):
        response = self.client.get(reverse("listings-list"), {"price_type": Listing.PriceType.FREE})
        self.assertEqual(response.status_code, 200)
        ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.lamp.pk, ids)
        self.assertNotIn(self.fan.pk, ids)

    def test_sort_newest(self):
        response = self.client.get(reverse("listings-list"), {"sort": "newest"})
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 2)

    def test_sort_price_low(self):
        response = self.client.get(reverse("listings-list"), {"sort": "price_low"})
        self.assertEqual(response.status_code, 200)

    def test_sort_price_high(self):
        response = self.client.get(reverse("listings-list"), {"sort": "price_high"})
        self.assertEqual(response.status_code, 200)

    def test_status_active_filter(self):
        response = self.client.get(reverse("listings-list"), {"status": "active"})
        self.assertEqual(response.status_code, 200)

    def test_overdue_available_listing_excluded_from_browse(self):
        # A listing still marked AVAILABLE but past its deadline should be
        # soft-filtered out of browse results even before the cron runs.
        overdue = Listing.objects.create(
            owner=self.user, title="Old Chair", description="Past deadline",
            category=Listing.Category.OTHER, condition=Listing.Condition.FAIR,
            price_type=Listing.PriceType.FREE, pickup_zone="Basement",
            available_until=timezone.now() - timedelta(hours=1),
            status=Listing.Status.AVAILABLE,
        )
        response = self.client.get(reverse("listings-list"))
        self.assertEqual(response.status_code, 200)
        ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(overdue.pk, ids)

    def test_mine_filter_shows_only_own_listings(self):
        other = User.objects.create_user(
            email="other2@example.com", password="pass", display_name="Other",
            campus_name="Test U", email_verified=True,
        )
        other_listing = Listing.objects.create(
            owner=other, title="Stranger Lamp", description="Someone else's",
            category=Listing.Category.LIGHTING, condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE, pickup_zone="Far", available_until=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(reverse("listings-list"), {"mine": "1"})
        self.assertEqual(response.status_code, 200)
        ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(other_listing.pk, ids)


# ---------------------------------------------------------------------------
# Publish flow unit tests
# ---------------------------------------------------------------------------

class PublishFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="pub@example.com", password="pass", display_name="Pub",
            campus_name="Test U", email_verified=True,
        )
        self.session = RoomScanSession.objects.create(
            owner=self.user,
            name="Pub Session",
            pickup_zone="South Hall",
            move_out_deadline=timezone.now() + timedelta(days=2),
        )

    def _make_item(self, **kwargs):
        defaults = {
            "scan_session": self.session,
            "title": "Test Item",
            "category": Listing.Category.LIGHTING,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.FREE,
            "triage_status": RoomScanItemDraft.TriageStatus.SELL,
            "estimated_retail_value": "10.00",
        }
        defaults.update(kwargs)
        return RoomScanItemDraft(**defaults)

    def test_infer_preset_key_fan(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="Cooling fan")
        self.assertEqual(infer_preset_key(item), "fan")

    def test_infer_preset_key_mirror(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="Bathroom mirror")
        self.assertEqual(infer_preset_key(item), "mirror")

    def test_infer_preset_key_hangers(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="Plastic hangers 10 pack")
        self.assertEqual(infer_preset_key(item), "hangers")

    def test_infer_preset_key_school_supplies(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="School supply kit", category=Listing.Category.SUPPLIES)
        self.assertIn(infer_preset_key(item), ["school_supplies"])

    def test_infer_preset_key_storage_bin(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="Storage bin", category=Listing.Category.STORAGE)
        self.assertEqual(infer_preset_key(item), "storage_bin")

    def test_infer_preset_key_toiletries(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="Toiletry bundle", category=Listing.Category.TOILETRIES)
        self.assertEqual(infer_preset_key(item), "toiletries_bundle")

    def test_infer_preset_key_uses_preset_key_when_set(self):
        from listings.publish_flow import infer_preset_key
        item = self._make_item(title="Random thing", preset_key="desk_lamp")
        self.assertEqual(infer_preset_key(item), "desk_lamp")

    def test_build_missing_fields_flags_missing_title(self):
        from listings.publish_flow import build_missing_fields
        item = self._make_item(title="")
        self.assertIn("title", build_missing_fields(item, self.session))

    def test_build_missing_fields_flags_low_cost_without_price(self):
        from listings.publish_flow import build_missing_fields
        item = self._make_item(price_type=Listing.PriceType.LOW_COST, price_amount=None)
        self.assertIn("price_amount", build_missing_fields(item, self.session))

    def test_build_missing_fields_donate_path_checks_hub(self):
        from listings.publish_flow import build_missing_fields
        item = self._make_item(triage_status=RoomScanItemDraft.TriageStatus.DONATE)
        missing = build_missing_fields(item, self.session)
        self.assertIn("donation_hub", missing)

    def test_get_publish_readiness_donate_needs_info(self):
        from listings.publish_flow import get_publish_readiness
        item = self._make_item(triage_status=RoomScanItemDraft.TriageStatus.DONATE)
        self.assertEqual(get_publish_readiness(item, self.session), "needs_info")

    def test_get_recommended_action_keep(self):
        from listings.publish_flow import get_recommended_action
        item = self._make_item(triage_status=RoomScanItemDraft.TriageStatus.KEEP)
        self.assertEqual(get_recommended_action(item), "keep")

    def test_get_recommended_action_toss(self):
        from listings.publish_flow import get_recommended_action
        item = self._make_item(triage_status=RoomScanItemDraft.TriageStatus.TOSS)
        self.assertEqual(get_recommended_action(item), "toss")

    def test_get_recommended_action_review_fallback(self):
        from listings.publish_flow import get_recommended_action
        item = self._make_item(triage_status="")
        self.assertEqual(get_recommended_action(item), "review")

    def test_build_publish_description_uses_notes_first(self):
        from listings.publish_flow import build_publish_description
        item = self._make_item(notes="Hand-written note here.")
        self.assertEqual(build_publish_description(item), "Hand-written note here.")


# ---------------------------------------------------------------------------
# Reservation state machine edge cases
# ---------------------------------------------------------------------------

class ReservationStateMachineTests(APITestCase):
    def setUp(self):
        # Throttle counters live in the cache and key on user pk, which SQLite
        # reuses across tests — clear so earlier tests can't trip the limit here.
        from django.core.cache import cache
        cache.clear()
        self.owner = User.objects.create_user(
            email="owner_sm@example.com", password="pass", display_name="Owner",
            campus_name="Test U", email_verified=True,
        )
        self.claimant = User.objects.create_user(
            email="claimant_sm@example.com", password="pass", display_name="Claimant",
            campus_name="Test U", email_verified=True,
        )
        self.listing = Listing.objects.create(
            owner=self.owner, title="Yoga Mat", description="Clean.",
            category=Listing.Category.COMFORT, condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE, pickup_zone="Gym",
            available_until=timezone.now() + timedelta(days=2),
        )

    def _make_reservation(self):
        self.client.force_authenticate(self.claimant)
        resp = self.client.post(
            reverse("reservations-list"),
            {"listing": self.listing.pk, "pickup_time_window": "Monday 5–6 pm"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        return resp.data["id"]

    def test_claimant_cannot_confirm_their_own_reservation(self):
        rid = self._make_reservation()
        self.client.force_authenticate(self.claimant)
        resp = self.client.patch(
            reverse("reservations-detail", kwargs={"pk": rid}),
            {"status": Reservation.Status.CONFIRMED},
            format="json",
        )
        self.assertIn(resp.status_code, [400, 403])

    def test_owner_can_cancel_reservation(self):
        rid = self._make_reservation()
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            reverse("reservations-detail", kwargs={"pk": rid}),
            {"status": Reservation.Status.CANCELLED},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        res = Reservation.objects.get(pk=rid)
        self.assertEqual(res.status, Reservation.Status.CANCELLED)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.AVAILABLE)

    def test_reservation_list_only_returns_user_reservations(self):
        rid = self._make_reservation()
        stranger = User.objects.create_user(
            email="stranger_sm@example.com", password="pass", display_name="Stranger",
            campus_name="Test U", email_verified=True,
        )
        self.client.force_authenticate(stranger)
        resp = self.client.get(reverse("reservations-list"))
        self.assertEqual(resp.status_code, 200)
        ids = [r["id"] for r in resp.data["results"]]
        self.assertNotIn(rid, ids)

    def test_feedback_list_accessible_to_staff_without_participant_filter(self):
        rid = self._make_reservation()
        self.client.force_authenticate(self.owner)
        self.client.patch(reverse("reservations-detail", kwargs={"pk": rid}), {"status": "confirmed"}, format="json")
        self.client.patch(reverse("reservations-detail", kwargs={"pk": rid}), {"status": "completed"}, format="json")
        self.client.force_authenticate(self.owner)
        self.client.post(
            reverse("reservations-feedback", kwargs={"pk": rid}),
            {"rating": 4, "tags": ["friendly"], "note": "Smooth."},
            format="json",
        )
        staff = User.objects.create_user(
            email="staff_sm@example.com", password="pass", display_name="Staff",
            campus_name="Test U", email_verified=True, is_staff=True,
        )
        self.client.force_authenticate(staff)
        resp = self.client.get(reverse("reservations-feedback", kwargs={"pk": rid}))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Insights aggregations
# ---------------------------------------------------------------------------

class InsightsAggregationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="insights@example.com", password="pass", display_name="Insights",
            campus_name="Test U", email_verified=True,
        )
        self.client.force_authenticate(self.user)

    def test_insights_returns_200_for_authenticated_user(self):
        response = self.client.get(reverse("insights"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("my_impact", response.data)
        self.assertIn("campus_snapshot", response.data)

    def test_insights_user_without_campus(self):
        no_campus_user = User.objects.create_user(
            email="nocampus@example.com", password="pass", display_name="No Campus",
            email_verified=True,
        )
        self.client.force_authenticate(no_campus_user)
        response = self.client.get(reverse("insights"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("my_impact", response.data)

    def test_insights_counts_rescued_items(self):
        Listing.objects.create(
            owner=self.user, title="Rescued Chair", description="Picked up.",
            category=Listing.Category.OTHER, condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE, pickup_zone="Hall A",
            available_until=timezone.now() + timedelta(days=1),
            status=Listing.Status.PICKED_UP,
        )
        response = self.client.get(reverse("insights"))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data["my_impact"]["rescued_items"], 1)


# ---------------------------------------------------------------------------
# expire_listings management command
# ---------------------------------------------------------------------------

class ExpireListingsCommandTests(APITestCase):
    def test_expire_listings_command_marks_stale_listings(self):
        from io import StringIO
        user = User.objects.create_user(
            email="expirec@example.com", password="pass", display_name="Expire",
            campus_name="Test U", email_verified=True,
        )
        past = timezone.now() - timedelta(hours=3)
        stale = Listing.objects.create(
            owner=user, title="Stale Fan", description="Old.",
            category=Listing.Category.COMFORT, condition=Listing.Condition.FAIR,
            price_type=Listing.PriceType.FREE, pickup_zone="Hall",
            available_until=past, status=Listing.Status.AVAILABLE,
        )
        out = StringIO()
        call_command("expire_listings", stdout=out)
        stale.refresh_from_db()
        self.assertEqual(stale.status, Listing.Status.EXPIRED)
        self.assertIn("Expired", out.getvalue())


# ---------------------------------------------------------------------------
# Rescue request filter/search edge cases
# ---------------------------------------------------------------------------

class RescueRequestFilterTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="reqfilter@example.com", password="pass", display_name="Req",
            campus_name="Test U", email_verified=True,
        )
        self.client.force_authenticate(self.user)
        self.client.post(
            reverse("requests-list"),
            {
                "title": "Need a Lamp",
                "description": "Need lighting.",
                "category": Listing.Category.LIGHTING,
                "pickup_zone": "Dorm A",
                "urgency": RescueRequest.Urgency.FLEXIBLE,
                "needed_by": (timezone.now() + timedelta(days=5)).isoformat(),
            },
            format="json",
        )

    def test_search_rescue_requests(self):
        response = self.client.get(reverse("requests-list"), {"search": "lamp"})
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 1)

    def test_category_filter_rescue_requests(self):
        response = self.client.get(reverse("requests-list"), {"category": Listing.Category.LIGHTING})
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 1)

    def test_mine_filter_rescue_requests(self):
        other = User.objects.create_user(
            email="reqother@example.com", password="pass", display_name="Other",
            campus_name="Test U", email_verified=True,
        )
        self.client.force_authenticate(other)
        response = self.client.get(reverse("requests-list"), {"mine": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

    def test_status_filter_rescue_requests(self):
        response = self.client.get(reverse("requests-list"), {"status": RescueRequest.Status.OPEN})
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Listing gallery (multi-image)
# ---------------------------------------------------------------------------

class ListingGalleryTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="gallery-owner@example.com",
            password="pass",
            display_name="Gallery Owner",
            email_verified=True,
        )
        self.other = User.objects.create_user(
            email="gallery-other@example.com",
            password="pass",
            display_name="Someone Else",
            email_verified=True,
        )
        self.listing = Listing.objects.create(
            owner=self.owner,
            title="Bookshelf",
            description="Solid wood shelf.",
            category=Listing.Category.STORAGE,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="North lobby",
            available_until=timezone.now() + timedelta(days=3),
        )

    def test_owner_can_upload_gallery_images(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/listings/{self.listing.pk}/images",
            {"images": [jpeg_upload("a.jpg"), jpeg_upload("b.jpg")]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["listing"]["gallery"]), 2)
        positions = [img["position"] for img in response.data["listing"]["gallery"]]
        self.assertEqual(positions, sorted(positions))

    def test_non_owner_cannot_upload_gallery_images(self):
        self.client.force_authenticate(self.other)
        response = self.client.post(
            f"/api/listings/{self.listing.pk}/images",
            {"images": [jpeg_upload()]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 404)

    def test_gallery_cap_enforced(self):
        from .models import ListingImage
        self.client.force_authenticate(self.owner)
        for i in range(4):
            ListingImage.objects.create(listing=self.listing, image=jpeg_upload(f"g{i}.jpg"), position=i)
        response = self.client.post(
            f"/api/listings/{self.listing.pk}/images",
            {"images": [jpeg_upload("overflow.jpg")]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_owner_can_delete_gallery_image(self):
        from .models import ListingImage
        img = ListingImage.objects.create(listing=self.listing, image=jpeg_upload(), position=0)
        self.client.force_authenticate(self.owner)
        response = self.client.delete(f"/api/listings/{self.listing.pk}/images/{img.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["listing"]["gallery"], [])
        self.assertFalse(ListingImage.objects.filter(pk=img.pk).exists())

    def test_gallery_serialized_on_detail_but_not_list(self):
        from .models import ListingImage
        ListingImage.objects.create(listing=self.listing, image=jpeg_upload(), position=0)
        self.client.force_authenticate(self.other)
        detail = self.client.get(f"/api/listings/{self.listing.pk}")
        self.assertEqual(len(detail.data["gallery"]), 1)
        listing_page = self.client.get("/api/listings")
        row = next(r for r in listing_page.data["results"] if r["id"] == self.listing.pk)
        self.assertEqual(row["gallery"], [])


# ---------------------------------------------------------------------------
# Async AI detection status protocol
# ---------------------------------------------------------------------------

class AiDetectAsyncTests(APITestCase):
    def setUp(self):
        # LocMemCache survives across tests and SQLite reuses pks — clear so
        # a previous test's detect status can't leak into this one.
        from django.core.cache import cache
        cache.clear()
        self.owner = User.objects.create_user(
            email="ai-owner@example.com",
            password="pass",
            display_name="AI Owner",
            email_verified=True,
        )
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

    def test_detect_runs_eagerly_and_creates_drafts(self):
        from unittest.mock import patch

        fake_items = [{
            "title": "Desk lamp",
            "category": "lighting",
            "condition": "good",
            "price_type": "low_cost",
            "estimated_retail_value": 24.0,
            "hotspot_box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        }]
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), \
                patch("listings.ai_detect.detect_items_in_image", return_value=fake_items):
            response = self.client.post(
                f"/api/scan-sessions/{self.session.pk}/ai-detect",
                {"image_id": self.image.pk},
                format="json",
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "done")
        self.assertEqual(response.data["created_count"], 1)
        self.assertEqual(self.session.items.count(), 1)

        # Status endpoint reports the finished run
        status_resp = self.client.get(
            f"/api/scan-sessions/{self.session.pk}/ai-detect/status",
            {"image_id": self.image.pk},
        )
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.data["status"], "done")

    def test_detect_failure_reported_not_raised(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), \
                patch("listings.ai_detect.detect_items_in_image", side_effect=RuntimeError("model down")):
            response = self.client.post(
                f"/api/scan-sessions/{self.session.pk}/ai-detect",
                {"image_id": self.image.pk},
                format="json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("AI detection failed", response.data["detail"])
        self.assertEqual(self.session.items.count(), 0)

    def test_status_unknown_when_never_started(self):
        response = self.client.get(
            f"/api/scan-sessions/{self.session.pk}/ai-detect/status",
            {"image_id": self.image.pk},
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Listing thumbnail renditions
# ---------------------------------------------------------------------------

class ListingThumbnailTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="thumb-owner@example.com",
            password="pass",
            display_name="Thumb Owner",
            email_verified=True,
        )

    def _create_listing(self):
        return Listing.objects.create(
            owner=self.owner,
            title="Desk Lamp",
            description="Bright and working.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="North lobby",
            available_until=timezone.now() + timedelta(days=3),
            image=jpeg_upload("cover.jpg"),
        )

    def test_thumbnail_generated_on_create(self):
        listing = self._create_listing()
        listing.refresh_from_db()
        self.assertTrue(listing.image_thumb)
        self.assertIn("listing-thumbs/", listing.image_thumb.name)

    def test_thumb_url_serialized(self):
        self.client.force_authenticate(self.owner)
        listing = self._create_listing()
        response = self.client.get(f"/api/listings/{listing.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("listing-thumbs", response.data["thumb_url"])
        self.assertIn("listing-images", response.data["image_url"])

    def test_replacing_cover_regenerates_thumbnail(self):
        self.client.force_authenticate(self.owner)
        listing = self._create_listing()
        listing.refresh_from_db()
        original_thumb = listing.image_thumb.name

        response = self.client.patch(
            f"/api/listings/{listing.pk}",
            {"image": jpeg_upload("replacement.jpg")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        listing.refresh_from_db()
        self.assertTrue(listing.image_thumb)
        self.assertNotEqual(listing.image_thumb.name, original_thumb)

    def test_listing_without_image_has_no_thumb(self):
        listing = Listing.objects.create(
            owner=self.owner,
            title="No photo",
            description="Text only.",
            category=Listing.Category.OTHER,
            condition=Listing.Condition.FAIR,
            price_type=Listing.PriceType.FREE,
            pickup_zone="South lobby",
            available_until=timezone.now() + timedelta(days=3),
        )
        listing.refresh_from_db()
        self.assertFalse(listing.image_thumb)


class AiClientTests(APITestCase):
    """The shared Gemini client: parsing contract, availability, fetch guards."""

    def test_parse_model_json_tolerates_fences_and_prose(self):
        from .ai_client import parse_model_json

        self.assertEqual(parse_model_json('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_model_json('```json\n[1, 2]\n```'), [1, 2])
        self.assertEqual(parse_model_json('Sure! Here you go: {"status": "approved"} hope that helps'),
                         {"status": "approved"})
        self.assertIsNone(parse_model_json("no json here at all"))
        self.assertIsNone(parse_model_json(""))

    def test_ai_available_reads_either_gemini_env_var(self):
        from unittest import mock

        from .ai_client import ai_available

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            self.assertFalse(ai_available())
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}):
            self.assertTrue(ai_available())
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": "k"}):
            self.assertTrue(ai_available())

    def test_generate_helpers_raise_runtime_error_without_key(self):
        from unittest import mock

        from .ai_client import AiNotConfigured, generate_json, generate_text

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            with self.assertRaises(AiNotConfigured):
                generate_text("hi")
            with self.assertRaises(RuntimeError):  # AiNotConfigured IS a RuntimeError
                generate_json("hi")

    def test_generate_json_requests_json_mime_and_parses(self):
        from types import SimpleNamespace
        from unittest import mock

        from . import ai_client

        part = SimpleNamespace(text='{"status": "flagged", "reason": "spam"}', function_call=None)
        response = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))]
        )
        fake_models = mock.Mock()
        fake_models.generate_content.return_value = response
        fake_client = SimpleNamespace(models=fake_models)

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(ai_client, "_client", return_value=fake_client):
            parsed = ai_client.generate_json("moderate this", max_tokens=150)

        self.assertEqual(parsed, {"status": "flagged", "reason": "spam"})
        kwargs = fake_models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["config"]["response_mime_type"], "application/json")
        # Requested budget + 256 headroom: thinking tokens count toward
        # max_output_tokens and must not starve the visible answer.
        self.assertEqual(kwargs["config"]["max_output_tokens"], 150 + 256)
        self.assertEqual(kwargs["config"]["thinking_config"], {"thinking_level": "low"})

    def test_fetch_image_bytes_guards_scheme_content_type_and_size(self):
        from unittest import mock

        from .ai_client import fetch_image_bytes

        with self.assertRaises(ValueError):
            fetch_image_bytes("file:///etc/passwd")
        with self.assertRaises(ValueError):
            fetch_image_bytes("ftp://example.com/x.png")

        def fake_response(content_type, chunks):
            response = mock.Mock()
            response.headers = {"Content-Type": content_type}
            response.iter_content.return_value = iter(chunks)
            response.raise_for_status.return_value = None
            return response

        with mock.patch("requests.get", return_value=fake_response("text/html", [b"<html>"])):
            with self.assertRaises(ValueError):
                fetch_image_bytes("https://example.com/page")

        with mock.patch("requests.get", return_value=fake_response("image/png", [b"x" * 1024] * 3)):
            with self.assertRaises(ValueError):
                fetch_image_bytes("https://example.com/huge.png", max_bytes=2048)

        with mock.patch("requests.get", return_value=fake_response("image/png; charset=binary", [b"png-bytes"])):
            data, mime = fetch_image_bytes("https://example.com/ok.png")
        self.assertEqual((data, mime), (b"png-bytes", "image/png"))


class ProductionEmailMediaTests(APITestCase):
    """Email deliverability headers + storage-agnostic file handling."""

    def test_transactional_emails_carry_one_click_unsubscribe_headers(self):
        from django.core import mail

        from .transactional_emails import _send

        user = User.objects.create_user(email="digest@test.edu", password="pw-Str0ng!x")
        _send(subject="Test", message="Body", recipient=user.email, user=user)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("List-Unsubscribe", sent.extra_headers)
        self.assertTrue(sent.extra_headers["List-Unsubscribe"].startswith("<http"))
        self.assertEqual(sent.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertIn("To stop receiving emails from ReNest", sent.body)

    def test_system_emails_without_user_send_plain(self):
        from django.core import mail

        from .transactional_emails import _send

        _send(subject="Ops", message="Body", recipient="ops@test.edu")
        self.assertEqual(mail.outbox[0].extra_headers, {})

    def test_email_respects_notification_opt_out(self):
        from django.core import mail

        from .transactional_emails import _send

        user = User.objects.create_user(email="optout@test.edu", password="pw-Str0ng!x")
        user.email_notifications = False
        _send(subject="Test", message="Body", recipient=user.email, user=user)
        self.assertEqual(mail.outbox, [])

    def test_detect_items_accepts_file_objects_not_just_paths(self):
        """Remote storage (S3/R2) FieldFiles have no .path — detection must
        read through the file API."""
        import io
        from unittest import mock

        from django.core.files.base import File

        from .ai_detect import detect_items_in_image

        fake_file = File(io.BytesIO(b"png-bytes"), name="room-shots/a.png")
        with mock.patch("listings.ai_detect.generate_vision_json", return_value=[]) as gen:
            items = detect_items_in_image(fake_file)

        self.assertEqual(items, [])
        image_bytes, mime_type, _prompt = gen.call_args.args
        self.assertEqual(image_bytes, b"png-bytes")
        self.assertEqual(mime_type, "image/png")

    def test_detect_items_still_accepts_paths(self):
        import tempfile
        from unittest import mock

        from .ai_detect import detect_items_in_image

        with tempfile.NamedTemporaryFile(suffix=".webp") as fh:
            fh.write(b"webp-bytes")
            fh.flush()
            with mock.patch("listings.ai_detect.generate_vision_json", return_value=[{"title": "Lamp"}]) as gen:
                items = detect_items_in_image(fh.name)

        self.assertEqual(items, [{"title": "Lamp"}])
        self.assertEqual(gen.call_args.args[1], "image/webp")


class ReservationChatVisibilityTests(APITestCase):
    """Unread message tracking + notification deep-links that actually resolve."""

    def setUp(self):
        self.owner = User.objects.create_user(email="owner-chat@test.edu", password="pw-Str0ng!x")
        self.claimant = User.objects.create_user(email="claimant-chat@test.edu", password="pw-Str0ng!x")
        self.listing = Listing.objects.create(
            owner=self.owner,
            title="Chat lamp",
            description="Works.",
            category=Listing.Category.LIGHTING,
            condition=Listing.Condition.GOOD,
            price_type=Listing.PriceType.FREE,
            pickup_zone="Maple Hall lobby",
            available_until=timezone.now() + timedelta(days=2),
        )
        self.reservation = Reservation.objects.create(
            listing=self.listing, claimant=self.claimant, pickup_time_window="Fri 3-5pm"
        )

    def _my_reservation(self, client):
        payload = client.get("/api/reservations").json()
        rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        return next(r for r in rows if r["id"] == self.reservation.id)

    def test_unread_badge_counts_and_clears_on_read(self):
        self.client.force_login(self.claimant)
        response = self.client.post(
            f"/api/reservations/{self.reservation.id}/messages",
            {"body": "Still good for Friday?"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        # Sender sees no unread; the owner sees exactly one.
        self.assertEqual(self._my_reservation(self.client)["unread_messages"], 0)
        self.client.force_login(self.owner)
        self.assertEqual(self._my_reservation(self.client)["unread_messages"], 1)

        # Opening the thread marks it read...
        self.assertEqual(
            self.client.get(f"/api/reservations/{self.reservation.id}/messages").status_code, 200
        )
        self.assertEqual(self._my_reservation(self.client)["unread_messages"], 0)
        # ...without touching the sender's copy semantics.
        message = ReservationMessage.objects.get(reservation=self.reservation)
        self.assertIsNotNone(message.read_at)

    def test_chat_notification_links_to_a_route_that_exists(self):
        self.client.force_login(self.claimant)
        self.client.post(
            f"/api/reservations/{self.reservation.id}/messages", {"body": "hi"}, format="json"
        )
        notification = Notification.objects.get(user=self.owner, type=Notification.Type.CHAT)
        # Regression guard: these previously pointed at /handoff/<id>, a 404.
        self.assertEqual(notification.link_path, f"/handoffs/{self.reservation.id}")


class WriteOnlyThrottleTests(APITestCase):
    """Write quotas must never be consumed — or enforced — by reads.

    Regression: ListingCreateThrottle/ReservationCreateThrottle sit on
    combined list+create views, and DRF applies view throttles to every
    method, so browsing GETs used to drain the POST quotas and lock users
    out of reading the marketplace for the rest of the hour window.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.user = get_user_model().objects.create_user(
            email="throttled@example.com",
            password="secretpass123",
            display_name="Throttled",
            campus_name="Pacific State",
            email_verified=True,
        )
        self.client.force_authenticate(self.user)

    def test_reads_bypass_write_quotas_but_writes_still_count(self):
        from unittest import mock

        from .throttles import ListingCreateThrottle, ReservationCreateThrottle

        # Exhausted-from-the-start quota: any request the throttle actually
        # inspects is rejected, so a 200 proves the method was exempt.
        with (
            mock.patch.object(ListingCreateThrottle, "rate", "0/hour", create=True),
            mock.patch.object(ReservationCreateThrottle, "rate", "0/hour", create=True),
        ):
            for _ in range(3):
                response = self.client.get(reverse("listings-list"))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
            response = self.client.get(reverse("reservations-list"))
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            response = self.client.post(reverse("listings-list"), {}, format="json")
            self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class SentenceTrimAndCapRetryTests(APITestCase):
    def test_trim_to_sentence_drops_clipped_fragment(self):
        from .ai_client import trim_to_sentence

        self.assertEqual(
            trim_to_sentence("Great lamp. It will give the next student"),
            "Great lamp.",
        )
        self.assertEqual(trim_to_sentence("Works well!"), "Works well!")
        # Decimals are not sentence ends; fragments without any terminator
        # come back unchanged rather than empty.
        self.assertEqual(
            trim_to_sentence("Holds 1.5 gallons of anything"),
            "Holds 1.5 gallons of anything",
        )

    def test_generate_text_retries_with_double_budget_on_token_cap(self):
        from types import SimpleNamespace
        from unittest import mock

        from . import ai_client

        def fake_response(text, finish_reason):
            part = SimpleNamespace(text=text)
            return SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(parts=[part]),
                        finish_reason=finish_reason,
                    )
                ]
            )

        fake_models = mock.Mock()
        fake_models.generate_content.side_effect = [
            fake_response("Clipped mid", "FinishReason.MAX_TOKENS"),
            fake_response("Full sentence.", "FinishReason.STOP"),
        ]
        fake_client = SimpleNamespace(models=fake_models)

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k"}), \
                mock.patch.object(ai_client, "_client", return_value=fake_client):
            out = ai_client.generate_text("write a description", max_tokens=300)

        self.assertEqual(out, "Full sentence.")
        self.assertEqual(fake_models.generate_content.call_count, 2)
        first = fake_models.generate_content.call_args_list[0].kwargs["config"]
        second = fake_models.generate_content.call_args_list[1].kwargs["config"]
        self.assertEqual(first["max_output_tokens"], 300 + 256)
        self.assertEqual(second["max_output_tokens"], 600 + 256)
