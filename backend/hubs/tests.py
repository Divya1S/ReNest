from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import DonationHub


class DonationHubApiTests(APITestCase):
    def setUp(self):
        DonationHub.objects.all().delete()

    def test_preview_only_returns_demo_hubs(self):
        DonationHub.objects.create(
            name="Maple Hall Hub",
            campus_name="Pacific State",
            zone_label="North Quad",
            description="Front desk collection zone.",
            open_instructions="Bring boxed items between 4pm and 7pm.",
            active=True,
            is_demo=True,
        )
        DonationHub.objects.create(
            name="Internal Hub",
            campus_name="Pacific State",
            zone_label="Staff Only",
            description="Not for preview mode.",
            open_instructions="Private.",
            active=True,
            is_demo=False,
        )

        response = self.client.get(reverse("preview-hubs"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Maple Hall Hub")

    def test_public_hubs_endpoint_returns_non_demo_hubs(self):
        DonationHub.objects.create(
            name="Campus Reuse Closet",
            campus_name="Pacific State",
            zone_label="Union East",
            description="Open pickup shelves for small dorm goods.",
            open_instructions="Bring your student ID and your item confirmation.",
            active=True,
            is_demo=False,
        )

        response = self.client.get(reverse("hubs-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Campus Reuse Closet")


class MyManagedHubsTests(APITestCase):
    """GET /api/hubs/mine: only hubs the requester manages, auth required.

    Regression: the frontend dispatch console queried this endpoint while it
    didn't exist (404 → console stuck on "no hub").
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import HubManager

        self.manager = get_user_model().objects.create_user(
            email="hubmgr@example.com",
            password="testpass123",
            display_name="Hub Manager",
            campus_name="Pacific State",
        )
        self.hub = DonationHub.objects.create(
            name="Reuse Closet",
            campus_name="Pacific State",
            zone_label="Union East",
            description="Staffed donation zone.",
            open_instructions="Drop between 4-8pm.",
            active=True,
        )
        DonationHub.objects.create(
            name="Other Hub",
            campus_name="Pacific State",
            zone_label="West",
            description="Not mine.",
            open_instructions="",
            active=True,
        )
        HubManager.objects.create(hub=self.hub, user=self.manager)

    def test_returns_only_managed_hubs(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get(reverse("hubs-mine"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [row["name"] for row in response.data["results"]]
        self.assertEqual(names, ["Reuse Closet"])

    def test_requires_authentication(self):
        response = self.client.get(reverse("hubs-mine"))
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class HubManageAccessTests(APITestCase):
    """GET/PATCH /api/hubs/<pk>/manage refused staff, while every other hub operation lets staff through.

    listings.views.logistics.is_hub_manager treats staff as able to act on any
    hub; the two checks have to agree.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import HubManager

        User = get_user_model()
        self.hub = DonationHub.objects.create(
            name="Reuse Closet",
            campus_name="Pacific State",
            zone_label="Union East",
            description="Staffed donation zone.",
            open_instructions="Drop between 4-8pm.",
            active=True,
        )
        self.manager = User.objects.create_user(email="mgr@example.com", password="testpass123", display_name="Mgr")
        self.staff = User.objects.create_user(
            email="staff@example.com", password="testpass123", display_name="Staff", is_staff=True
        )
        self.student = User.objects.create_user(email="student@example.com", password="testpass123", display_name="Stu")
        HubManager.objects.create(hub=self.hub, user=self.manager)
        self.url = reverse("hubs-manage", kwargs={"pk": self.hub.pk})

    def test_staff_can_read_and_update_any_hub(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_200_OK)
        response = self.client.patch(self.url, {"open_instructions": "Closed for finals."}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.open_instructions, "Closed for finals.")
        # Being allowed to act is not the same as being listed as a manager.
        self.assertFalse(response.data["is_manager"])

    def test_staff_rule_matches_the_logistics_check(self):
        from listings.views.logistics import is_hub_manager

        for user in (self.staff, self.manager, self.student):
            with self.subTest(user=user.email):
                self.client.force_authenticate(user)
                allowed_here = self.client.get(self.url).status_code == status.HTTP_200_OK
                self.assertEqual(allowed_here, is_hub_manager(user, self.hub.pk))

    def test_manager_keeps_access(self):
        self.client.force_authenticate(self.manager)
        response = self.client.patch(self.url, {"capacity": 40}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_manager"])

    def test_other_students_are_still_refused(self):
        self.client.force_authenticate(self.student)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.patch(self.url, {"open_instructions": "Mine now."}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.hub.refresh_from_db()
        self.assertEqual(self.hub.open_instructions, "Drop between 4-8pm.")


class HubListQueryCountTests(APITestCase):
    """is_manager ran one EXISTS query per hub, so the list cost grew with the number of hubs."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        DonationHub.objects.all().delete()
        self.user = get_user_model().objects.create_user(
            email="viewer@example.com", password="testpass123", display_name="Viewer"
        )
        self.client.force_authenticate(self.user)

    def _make_hubs(self, count, *, is_demo=False):
        from .models import HubManager

        start = DonationHub.objects.count()
        hubs = [
            DonationHub.objects.create(
                name=f"Hub {start + index:02d}",
                campus_name="Pacific State",
                zone_label=f"Zone {start + index}",
                description="Collection point.",
                open_instructions="Open daily.",
                active=True,
                is_demo=is_demo,
            )
            for index in range(count)
        ]
        # Manage every other hub so both answers of is_manager are exercised.
        for hub in hubs[::2]:
            HubManager.objects.create(hub=hub, user=self.user)
        return hubs

    def _query_count(self, url_name):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(captured), response.data["results"]

    def test_list_query_count_does_not_grow_with_hub_count(self):
        self._make_hubs(2)
        with_two, rows = self._query_count("hubs-list")
        self.assertEqual(len(rows), 2)

        self._make_hubs(4)
        with_six, rows = self._query_count("hubs-list")
        self.assertEqual(len(rows), 6)

        self.assertEqual(with_two, with_six)

    def test_is_manager_values_are_still_correct(self):
        hubs = self._make_hubs(6)
        _, rows = self._query_count("hubs-list")
        managed = {hub.pk for hub in hubs[::2]}
        self.assertEqual({row["id"] for row in rows if row["is_manager"]}, managed)
        self.assertEqual({row["id"] for row in rows if not row["is_manager"]}, {hub.pk for hub in hubs} - managed)

    def test_other_hub_lists_are_constant_too(self):
        for url_name, is_demo in (("hubs-mine", False), ("preview-hubs", True)):
            with self.subTest(endpoint=url_name):
                DonationHub.objects.all().delete()
                self._make_hubs(2, is_demo=is_demo)
                small, _ = self._query_count(url_name)
                self._make_hubs(4, is_demo=is_demo)
                large, _ = self._query_count(url_name)
                self.assertEqual(small, large)

    def test_anonymous_viewer_is_never_a_manager(self):
        self._make_hubs(2)
        self.client.force_authenticate(None)
        _, rows = self._query_count("hubs-list")
        self.assertEqual([row["is_manager"] for row in rows], [False, False])
