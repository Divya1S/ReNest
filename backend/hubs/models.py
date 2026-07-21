from __future__ import annotations

from django.conf import settings
from django.db import models


class DonationHub(models.Model):
    name = models.CharField(max_length=140)
    campus_name = models.CharField(max_length=140)
    zone_label = models.CharField(max_length=140)
    description = models.TextField()
    open_instructions = models.TextField()
    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum number of items the hub can hold at once. Null = unlimited.",
    )
    active = models.BooleanField(default=True)
    is_demo = models.BooleanField(default=False)

    class Meta:
        ordering = ("campus_name", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.zone_label})"


class HubManager(models.Model):
    hub = models.ForeignKey(DonationHub, on_delete=models.CASCADE, related_name="hub_managers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="managed_hubs")

    class Meta:
        unique_together = (("hub", "user"),)

    def __str__(self) -> str:
        return f"{self.user} → {self.hub}"
