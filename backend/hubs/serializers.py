from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import DonationHub, HubManager

_MANAGED_HUB_IDS_ATTR = "_managed_hub_ids"


def managed_hub_ids(request: Any) -> frozenset[int]:
    """Ids of the hubs the requesting user manages, loaded once per request.

    is_manager used to run one EXISTS query per serialized hub, so a list of N
    hubs (or N listings that each nest a hub) cost N extra queries for every
    signed-in viewer. A user manages a handful of hubs at most, so the whole
    set is fetched with a single query and remembered on the request object,
    which every serializer in the response shares.
    """
    cached = getattr(request, _MANAGED_HUB_IDS_ATTR, None)
    if cached is None:
        cached = frozenset(
            HubManager.objects.filter(user=request.user).values_list("hub_id", flat=True)
        )
        setattr(request, _MANAGED_HUB_IDS_ATTR, cached)
    return cached


class DonationHubSerializer(serializers.ModelSerializer):
    is_manager = serializers.SerializerMethodField()

    class Meta:
        model = DonationHub
        fields = (
            "id",
            "name",
            "campus_name",
            "zone_label",
            "description",
            "open_instructions",
            "capacity",
            "active",
            "is_manager",
        )
        read_only_fields = (
            "id",
            "name",
            "campus_name",
            "zone_label",
            "description",
            "active",
            "is_manager",
        )

    def get_is_manager(self, obj: DonationHub) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.pk in managed_hub_ids(request)


class HubManagerUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DonationHub
        fields = ("open_instructions", "capacity", "active")

    def validate_capacity(self, value: Any) -> Any:
        if value is not None and value < 0:
            raise serializers.ValidationError("Capacity must be a positive number.")
        return value
