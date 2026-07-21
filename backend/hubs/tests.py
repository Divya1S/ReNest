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
    """GET /api/hubs/mine — only hubs the requester manages, auth required.

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
