from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import DonationHub


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
        return obj.hub_managers.filter(user=request.user).exists()


class HubManagerUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DonationHub
        fields = ("open_instructions", "capacity", "active")

    def validate_capacity(self, value: Any) -> Any:
        if value is not None and value < 0:
            raise serializers.ValidationError("Capacity must be a positive number.")
        return value
