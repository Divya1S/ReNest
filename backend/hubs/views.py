from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404

from dormcycle.typed import current_user

if TYPE_CHECKING:
    from accounts.models import User
from rest_framework import generics, permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DonationHub
from .serializers import DonationHubSerializer, HubManagerUpdateSerializer


class DonationHubListView(generics.ListAPIView):
    serializer_class = DonationHubSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self) -> "QuerySet[DonationHub]":
        return DonationHub.objects.filter(active=True, is_demo=False)


class MyManagedHubsView(generics.ListAPIView):
    """Hubs where the requesting user is a hub manager (dispatch console)."""

    serializer_class = DonationHubSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> "QuerySet[DonationHub]":
        return DonationHub.objects.filter(
            hub_managers__user=current_user(self.request)
        ).distinct()


class DonationHubDetailView(generics.RetrieveAPIView):
    serializer_class = DonationHubSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self) -> "QuerySet[DonationHub]":
        return DonationHub.objects.filter(active=True)


class PreviewDonationHubListView(generics.ListAPIView):
    serializer_class = DonationHubSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self) -> "QuerySet[DonationHub]":
        return DonationHub.objects.filter(active=True, is_demo=True)


class HubManagerUpdateView(APIView):
    """PATCH open_instructions / capacity / active for hubs where the user is a manager."""

    permission_classes = [permissions.IsAuthenticated]

    def get_hub(self, pk: int, user: "User") -> DonationHub:
        hub = get_object_or_404(DonationHub, pk=pk, is_demo=False)
        if not hub.hub_managers.filter(user=user).exists():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You are not a manager of this hub.")
        return hub

    def get(self, request: Request, pk: int) -> Response:
        hub = self.get_hub(pk, current_user(request))
        return Response(DonationHubSerializer(hub, context={"request": request}).data)

    def patch(self, request: Request, pk: int) -> Response:
        hub = self.get_hub(pk, current_user(request))
        serializer = HubManagerUpdateSerializer(hub, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(DonationHubSerializer(hub, context={"request": request}).data)
