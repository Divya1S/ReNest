from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone
from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializers import UserSerializer
from hubs.serializers import DonationHubSerializer

from .tasks import (
    send_reservation_confirmed_email_task,
    send_reservation_completed_email_task,
)
from .models import (
    HandoffFeedback,
    Listing,
    ListingReport,
    ListingUpdate,
    MoveOutTask,
    Notification,
    Reservation,
    RescueRequest,
    RoomScanImage,
    ReservationMessage,
    RoomScanItemDraft,
    RoomScanSession,
    SavedListing,
)
from .image_utils import compress_image
from .publish_flow import build_missing_fields, build_suggestion_pack, get_publish_readiness, get_recommended_action
from .trust import build_user_trust_summary


_PENDING = frozenset([
    RoomScanItemDraft.TriageStatus.REVIEW,
    RoomScanItemDraft.TriageStatus.SELL,
    RoomScanItemDraft.TriageStatus.DONATE,
])
_HANDLED = frozenset([
    RoomScanItemDraft.TriageStatus.KEEP,
    RoomScanItemDraft.TriageStatus.TOSS,
    RoomScanItemDraft.TriageStatus.DONE,
])
_PUBLISH_CANDIDATE = frozenset([
    RoomScanItemDraft.TriageStatus.SELL,
    RoomScanItemDraft.TriageStatus.DONATE,
])


def compute_scan_summary(scan_session: RoomScanSession) -> dict[str, Any]:
    # Use .all() so callers that prefetched items (DashboardView, session list)
    # read from the cache instead of firing new queries per session.
    items = list(scan_session.items.all())

    total_items = len(items)
    pending_items = sum(1 for i in items if i.triage_status in _PENDING)
    handled_items = sum(1 for i in items if i.triage_status in _HANDLED)
    donation_count = sum(1 for i in items if i.donation_hub_id)

    # generated_listings is prefetched in DashboardView and the build_* helpers.
    rescued_listings = sum(
        1 for l in scan_session.generated_listings.all()
        if l.status != Listing.Status.EXPIRED
    )

    publish_candidates = [
        i for i in items
        if i.triage_status in _PUBLISH_CANDIDATE
        or i.linked_listing_id is not None
        or i.donation_hub_id is not None
    ]

    ready_to_publish_count = 0
    missing_info_count = 0
    donation_route_count = 0
    published_count = 0
    scans_blocked = False

    for item in publish_candidates:
        # Pass scan_session so get_publish_readiness never needs item.scan_session
        # FK lookup — items from prefetch cache lack that select_related.
        readiness = get_publish_readiness(item, scan_session)
        if readiness == "ready":
            ready_to_publish_count += 1
        elif readiness == "needs_info":
            missing_info_count += 1
            scans_blocked = True
        elif readiness == "published":
            published_count += 1
        elif readiness == "donation_route":
            donation_route_count += 1

    estimated_student_savings = sum(
        max(
            Decimal(i.estimated_retail_value or 0) - Decimal(i.price_amount or 0),
            Decimal("0.00"),
        )
        for i in items if i.linked_listing_id
    )

    return {
        "total_items": total_items,
        "pending_items": pending_items,
        "handled_items": handled_items,
        "rescued_items": rescued_listings,
        "donation_count": donation_count,
        "ready_to_publish_count": ready_to_publish_count,
        "missing_info_count": missing_info_count,
        "published_count": published_count,
        "donation_route_count": donation_route_count,
        "is_blocked_by_missing_info": scans_blocked,
        "estimated_student_savings": estimated_student_savings,
        "room_cleared_percent": scan_session.progress_percent,
    }


def compute_task_summary(scan_session: RoomScanSession) -> dict[str, Any]:
    now = timezone.now()
    # .all() uses prefetch cache when tasks were prefetched by the caller.
    tasks = list(scan_session.tasks.all())
    done = MoveOutTask.Status.DONE
    in_progress = MoveOutTask.Status.IN_PROGRESS
    total_tasks = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == done)
    in_progress_tasks = sum(1 for t in tasks if t.status == in_progress)
    overdue_tasks = sum(
        1 for t in tasks
        if t.status != done and t.due_at and t.due_at < now
    )
    due_today_tasks = sum(
        1 for t in tasks
        if t.status != done and t.due_at and now <= t.due_at <= now + timedelta(hours=24)
    )
    upcoming_tasks = sum(
        1 for t in tasks
        if t.status != done and t.due_at and t.due_at > now + timedelta(hours=24)
    )
    completion_percent = 0 if total_tasks == 0 else round((completed_tasks / total_tasks) * 100)
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "in_progress_tasks": in_progress_tasks,
        "overdue_tasks": overdue_tasks,
        "due_today_tasks": due_today_tasks,
        "upcoming_tasks": upcoming_tasks,
        "completion_percent": completion_percent,
    }


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        from .models import Organization
        model = Organization
        fields = ("id", "name", "cause_url", "campus_name", "venmo_handle", "verified")


class ListingSerializer(serializers.ModelSerializer):
    owner = UserSerializer(read_only=True)
    image_url = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()
    gallery = serializers.SerializerMethodField()
    is_urgent = serializers.SerializerMethodField()
    time_left_label = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_reserve = serializers.SerializerMethodField()
    can_post_update = serializers.SerializerMethodField()
    reservation_count = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    saved_count = serializers.SerializerMethodField()
    update_count = serializers.SerializerMethodField()
    view_count = serializers.SerializerMethodField()
    completeness_score = serializers.SerializerMethodField()
    repost_token = serializers.SerializerMethodField()
    moderation_flag_reason = serializers.SerializerMethodField()
    source_scan_item_id = serializers.SerializerMethodField()
    source_scan_name = serializers.SerializerMethodField()
    estimated_student_savings = serializers.SerializerMethodField()
    owner_trust_summary = serializers.SerializerMethodField()
    has_reported = serializers.SerializerMethodField()
    can_report = serializers.SerializerMethodField()
    donation_receipt_url = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = (
            "id",
            "owner",
            "source_scan_session",
            "source_scan_item_id",
            "source_scan_name",
            "title",
            "description",
            "category",
            "condition",
            "price_type",
            "price_amount",
            "estimated_retail_value",
            "estimated_student_savings",
            "owner_trust_summary",
            "pickup_zone",
            "building",
            "available_until",
            "status",
            "image",
            "image_cdn_url",
            "image_url",
            "thumb_url",
            "gallery",
            "is_urgent",
            "time_left_label",
            "can_edit",
            "can_reserve",
            "can_post_update",
            "reservation_count",
            "is_saved",
            "saved_count",
            "update_count",
            "view_count",
            "completeness_score",
            "repost_token",
            "moderation_status",
            "moderation_flag_reason",
            "condition_verified",
            "has_reported",
            "can_report",
            "donation_hub",
            "donation_receipt_url",
            "boosted_until",
            "quality_hints",
            "quality_hints_dismissed",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "owner",
            "source_scan_session",
            "source_scan_item_id",
            "source_scan_name",
            "estimated_student_savings",
            "owner_trust_summary",
            "status",
            "image_url",
            "thumb_url",
            "gallery",
            "is_urgent",
            "time_left_label",
            "can_edit",
            "can_reserve",
            "can_post_update",
            "reservation_count",
            "is_saved",
            "saved_count",
            "update_count",
            "view_count",
            "completeness_score",
            "repost_token",
            "moderation_status",
            "moderation_flag_reason",
            "condition_verified",
            "has_reported",
            "can_report",
            "donation_hub",
            "boosted_until",
            "quality_hints",
            "donation_receipt_url",
            "created_at",
            "updated_at",
        )

    def get_donation_receipt_url(self, obj: Listing) -> str | None:
        request = self.context.get("request")
        if obj.donation_receipt and request:
            return request.build_absolute_uri(obj.donation_receipt.url)
        return None

    def validate_image(self, value: Any) -> Any:
        if value:
            return compress_image(value)
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        price_type = attrs.get("price_type", getattr(self.instance, "price_type", None))
        price_amount = attrs.get("price_amount", getattr(self.instance, "price_amount", Decimal("0.00")))
        retail_value = attrs.get(
            "estimated_retail_value",
            getattr(self.instance, "estimated_retail_value", Decimal("0.00")),
        )
        available_until = attrs.get("available_until", getattr(self.instance, "available_until", None))

        if available_until and available_until <= timezone.now():
            raise serializers.ValidationError(
                {"available_until": "Move-out deadline must be in the future."}
            )

        if Decimal(retail_value) < Decimal("0.00"):
            raise serializers.ValidationError(
                {"estimated_retail_value": "Estimated retail value cannot be negative."}
            )

        if price_type == Listing.PriceType.FREE:
            attrs["price_amount"] = Decimal("0.00")
        elif price_type == Listing.PriceType.LOW_COST and Decimal(price_amount) <= Decimal("0.00"):
            raise serializers.ValidationError(
                {"price_amount": "Low-cost listings need a price greater than zero."}
            )

        return attrs

    def get_image_url(self, obj: Any) -> str | None:
        if obj.image_cdn_url:
            return obj.image_cdn_url
        if not obj.image:
            return None
        return obj.image.url

    def get_thumb_url(self, obj: Listing) -> str | None:
        # Card-grid rendition; falls back to the full image when no thumb
        # exists (CDN-hosted covers, seeded SVGs, pre-migration rows).
        if obj.image_thumb:
            return obj.image_thumb.url
        return self.get_image_url(obj)

    def update(self, instance: Listing, validated_data: dict[str, Any]) -> Listing:
        # Replacing the cover invalidates its rendition; the post_save signal
        # regenerates it from the new image.
        if "image" in validated_data:
            instance.image_thumb = None
        return super().update(instance, validated_data)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_gallery(self, obj: Listing) -> list[dict[str, Any]]:
        # Detail-only (like owner_trust_summary) so browse lists stay one query.
        if isinstance(self.parent, serializers.ListSerializer):
            return []
        return [
            {"id": img.pk, "image_url": img.image.url, "position": img.position}
            for img in obj.images.all()
            if img.image
        ]

    def get_is_urgent(self, obj: Any) -> bool:
        return obj.is_urgent

    def get_time_left_label(self, obj: Any) -> str:
        delta = obj.available_until - timezone.now()
        hours_left = int(delta.total_seconds() // 3600)
        if hours_left <= 0:
            return "Move-out window closed"
        if hours_left < 24:
            return f"{hours_left}h left"
        days_left = hours_left // 24
        return f"{days_left}d left"

    def get_can_edit(self, obj: Any) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.owner_id == request.user.id)

    def get_can_reserve(self, obj: Any) -> bool:
        request = self.context.get("request")
        return bool(
            request
            and request.user.is_authenticated
            and obj.owner_id != request.user.id
            and obj.status == Listing.Status.AVAILABLE
        )

    def get_reservation_count(self, obj: Any) -> int:
        v = getattr(obj, "reservation_count_ann", None)
        return v if v is not None else obj.reservations.exclude(status=Reservation.Status.CANCELLED).count()

    def get_can_post_update(self, obj: Any) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        if obj.owner_id == request.user.id:
            return True
        v = getattr(obj, "user_has_reservation_ann", None)
        return v if v is not None else obj.reservations.filter(claimant=request.user).exists()

    def get_is_saved(self, obj: Any) -> bool:
        v = getattr(obj, "is_saved_ann", None)
        if v is not None:
            return v
        request = self.context.get("request")
        return bool(
            request
            and request.user.is_authenticated
            and obj.saved_by.filter(user=request.user).exists()
        )

    def get_saved_count(self, obj: Any) -> int:
        v = getattr(obj, "saved_count_ann", None)
        return v if v is not None else obj.saved_by.count()

    def get_update_count(self, obj: Any) -> int:
        v = getattr(obj, "update_count_ann", None)
        return v if v is not None else obj.updates.count()

    def get_view_count(self, obj: Listing) -> int:
        v = getattr(obj, "view_count_ann", None)
        return v if v is not None else obj.view_events.count()

    def get_completeness_score(self, obj: Listing) -> int:
        v = getattr(obj, "completeness_score_ann", None)
        if v is not None:
            return v
        score = 0
        if obj.title and len(obj.title) >= 3:
            score += 20
        if obj.description and len(obj.description) >= 40:
            score += 20
        if obj.image:
            score += 20
        if obj.estimated_retail_value and obj.estimated_retail_value > 0:
            score += 20
        if obj.pickup_zone and len(obj.pickup_zone) >= 10:
            score += 20
        return score

    def get_repost_token(self, obj: Listing) -> str | None:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if obj.owner_id != request.user.id:
            return None
        if obj.status != Listing.Status.EXPIRED:
            return None
        return obj.repost_token or None

    def get_moderation_flag_reason(self, obj: Listing) -> str | None:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if obj.owner_id != request.user.id and not request.user.is_staff:
            return None
        if obj.moderation_status != Listing.ModerationStatus.FLAGGED:
            return None
        return obj.moderation_flag_reason or None

    def get_source_scan_item_id(self, obj: Any) -> int | None:
        draft = getattr(obj, "scan_draft_source", None)
        return draft.id if draft else None

    def get_source_scan_name(self, obj: Any) -> str | None:
        return obj.source_scan_session.name if obj.source_scan_session_id else None

    @extend_schema_field(OpenApiTypes.DECIMAL)
    def get_estimated_student_savings(self, obj: Listing) -> Decimal:
        return max(
            Decimal(obj.estimated_retail_value or 0) - Decimal(obj.price_amount or 0),
            Decimal("0.00"),
        )

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_owner_trust_summary(self, obj: Listing) -> dict[str, Any] | None:
        if isinstance(self.parent, serializers.ListSerializer):
            return None
        return build_user_trust_summary(obj.owner)

    def get_has_reported(self, obj: Any) -> bool:
        v = getattr(obj, "has_reported_ann", None)
        if v is not None:
            return v
        request = self.context.get("request")
        return bool(
            request
            and request.user.is_authenticated
            and obj.reports.filter(reporter=request.user).exists()
        )

    def get_can_report(self, obj: Any) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        if obj.owner_id == request.user.id or obj.is_demo:
            return False
        return not self.get_has_reported(obj)


class RoomScanImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = RoomScanImage
        fields = ("id", "image", "image_url", "position", "created_at")
        read_only_fields = ("id", "image_url", "created_at")

    def get_image_url(self, obj: Any) -> str | None:
        return obj.image.url if obj.image else None


class RoomScanItemDraftSerializer(serializers.ModelSerializer):
    source_image = serializers.PrimaryKeyRelatedField(
        queryset=RoomScanImage.objects.all(),
        required=False,
        allow_null=True,
    )
    donation_hub = serializers.PrimaryKeyRelatedField(
        queryset=DonationHubSerializer.Meta.model.objects.filter(active=True),
        required=False,
        allow_null=True,
    )
    source_image_detail = RoomScanImageSerializer(source="source_image", read_only=True)
    donation_hub_detail = DonationHubSerializer(source="donation_hub", read_only=True)
    linked_listing_detail = ListingSerializer(source="linked_listing", read_only=True)
    publish_readiness = serializers.SerializerMethodField()
    missing_fields = serializers.SerializerMethodField()
    recommended_action = serializers.SerializerMethodField()
    suggestion_pack = serializers.SerializerMethodField()
    preset_key = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = RoomScanItemDraft
        fields = (
            "id",
            "scan_session",
            "source_image",
            "source_image_detail",
            "hotspot_box",
            "title",
            "category",
            "condition",
            "price_type",
            "price_amount",
            "estimated_retail_value",
            "preset_key",
            "triage_status",
            "notes",
            "donation_hub",
            "donation_hub_detail",
            "linked_listing",
            "linked_listing_detail",
            "publish_readiness",
            "missing_fields",
            "recommended_action",
            "suggestion_pack",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "scan_session",
            "linked_listing",
            "linked_listing_detail",
            "publish_readiness",
            "missing_fields",
            "recommended_action",
            "suggestion_pack",
            "created_at",
            "updated_at",
        )

    def validate_hotspot_box(self, value: dict[str, Any]) -> dict[str, Any]:
        required_keys = {"x", "y", "width", "height"}
        if not required_keys.issubset(value.keys()):
            raise serializers.ValidationError("Hotspot box must include x, y, width, and height.")

        for key in required_keys:
            try:
                number = float(value[key])
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"Hotspot box value '{key}' must be numeric.") from None
            if number < 0 or number > 1:
                raise serializers.ValidationError("Hotspot coordinates must stay between 0 and 1.")

        if float(value["width"]) <= 0.02 or float(value["height"]) <= 0.02:
            raise serializers.ValidationError("Draw a slightly larger hotspot so the draft is visible.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        price_type = attrs.get("price_type", getattr(self.instance, "price_type", Listing.PriceType.FREE))
        price_amount = attrs.get("price_amount", getattr(self.instance, "price_amount", Decimal("0.00")))
        retail_value = attrs.get(
            "estimated_retail_value",
            getattr(self.instance, "estimated_retail_value", Decimal("0.00")),
        )

        if Decimal(retail_value) < Decimal("0.00"):
            raise serializers.ValidationError(
                {"estimated_retail_value": "Estimated retail value cannot be negative."}
            )

        if price_type == Listing.PriceType.FREE:
            attrs["price_amount"] = Decimal("0.00")
        elif Decimal(price_amount) < Decimal("0.00"):
            raise serializers.ValidationError(
                {"price_amount": "Low-cost rescue drafts cannot use a negative price."}
            )

        notes = attrs.get("notes")
        if notes is not None:
            attrs["notes"] = notes.strip()

        preset_key = attrs.get("preset_key")
        if preset_key == "":
            attrs["preset_key"] = None

        return attrs

    def get_publish_readiness(self, obj: Any) -> str:
        return get_publish_readiness(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_missing_fields(self, obj: RoomScanItemDraft) -> list[str]:
        return build_missing_fields(obj)

    def get_recommended_action(self, obj: RoomScanItemDraft) -> str | None:
        return get_recommended_action(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_suggestion_pack(self, obj: RoomScanItemDraft) -> dict[str, Any]:
        return build_suggestion_pack(obj)


class RoomScanSessionSerializer(serializers.ModelSerializer):
    images = RoomScanImageSerializer(many=True, read_only=True)
    items = RoomScanItemDraftSerializer(many=True, read_only=True)
    owner = UserSerializer(read_only=True)
    summary = serializers.SerializerMethodField()
    task_summary = serializers.SerializerMethodField()

    class Meta:
        model = RoomScanSession
        fields = (
            "id",
            "owner",
            "name",
            "status",
            "room_label",
            "room_type",
            "pickup_zone",
            "move_out_deadline",
            "progress_percent",
            "summary",
            "task_summary",
            "images",
            "items",
            "is_demo",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "owner",
            "status",
            "progress_percent",
            "summary",
            "task_summary",
            "images",
            "items",
            "is_demo",
            "created_at",
            "updated_at",
        )

    def validate_move_out_deadline(self, value: Any) -> Any:
        if value and value <= timezone.now():
            raise serializers.ValidationError("Move-out deadline must be in the future.")
        return value

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_summary(self, obj: RoomScanSession) -> dict[str, Any]:
        summary = compute_scan_summary(obj)
        summary["estimated_student_savings"] = f"{summary['estimated_student_savings']:.2f}"
        return summary

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_task_summary(self, obj: RoomScanSession) -> dict[str, Any]:
        return compute_task_summary(obj)


class MoveOutTaskSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()
    due_bucket = serializers.SerializerMethodField()
    scan_session_name = serializers.SerializerMethodField()

    class Meta:
        model = MoveOutTask
        fields = (
            "id",
            "scan_session",
            "title",
            "details",
            "category",
            "status",
            "due_at",
            "completed_at",
            "is_system",
            "system_key",
            "offset_hours",
            "is_overdue",
            "due_bucket",
            "scan_session_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "scan_session",
            "completed_at",
            "is_system",
            "system_key",
            "offset_hours",
            "is_overdue",
            "due_bucket",
            "scan_session_name",
            "created_at",
            "updated_at",
        )

    def validate_title(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Give this task a short title.")
        return value

    def validate_details(self, value: str) -> str:
        return value.strip()

    def create(self, validated_data: dict[str, Any]) -> MoveOutTask:
        task = super().create(validated_data)
        if task.status == MoveOutTask.Status.DONE and task.completed_at is None:
            task.completed_at = timezone.now()
            task.save(update_fields=["completed_at", "updated_at"])
        return task

    def update(self, instance: MoveOutTask, validated_data: dict[str, Any]) -> MoveOutTask:
        next_status = validated_data.get("status", instance.status)
        task = super().update(instance, validated_data)
        if next_status == MoveOutTask.Status.DONE and task.completed_at is None:
            task.completed_at = timezone.now()
            task.save(update_fields=["completed_at", "updated_at"])
        elif next_status != MoveOutTask.Status.DONE and task.completed_at is not None:
            task.completed_at = None
            task.save(update_fields=["completed_at", "updated_at"])
        return task

    def get_is_overdue(self, obj: Any) -> bool:
        return obj.is_overdue

    def get_due_bucket(self, obj: Any) -> str:
        if obj.status == MoveOutTask.Status.DONE:
            return "done"
        if not obj.due_at:
            return "unscheduled"
        now = timezone.now()
        if obj.due_at < now:
            return "overdue"
        if obj.due_at <= now + timedelta(hours=24):
            return "due_today"
        if obj.due_at <= now + timedelta(days=3):
            return "due_soon"
        return "upcoming"

    def get_scan_session_name(self, obj: Any) -> str | None:
        return obj.scan_session.name if obj.scan_session_id else None


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "type",
            "title",
            "body",
            "link_path",
            "priority",
            "is_read",
            "read_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "type",
            "title",
            "body",
            "link_path",
            "priority",
            "created_at",
        )

    def update(self, instance: Notification, validated_data: dict[str, Any]) -> Notification:
        next_is_read = validated_data.get("is_read", instance.is_read)
        instance = super().update(instance, validated_data)
        if next_is_read and instance.read_at is None:
            instance.read_at = timezone.now()
            instance.save(update_fields=["read_at"])
        elif not next_is_read and instance.read_at is not None:
            instance.read_at = None
            instance.save(update_fields=["read_at"])
        return instance


class ListingReportSerializer(serializers.ModelSerializer):
    reporter = UserSerializer(read_only=True)
    listing_title = serializers.SerializerMethodField()

    class Meta:
        model = ListingReport
        fields = (
            "id",
            "listing",
            "listing_title",
            "reporter",
            "reason",
            "details",
            "status",
            "reviewed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "reporter",
            "status",
            "reviewed_at",
            "created_at",
            "updated_at",
        )

    def validate_details(self, value: str) -> str:
        return value.strip()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        request = self.context["request"]
        listing = attrs.get("listing") or getattr(self.instance, "listing", None)

        if listing is None:
            raise serializers.ValidationError({"listing": "A listing is required."})
        if self.instance is None and listing.owner_id == request.user.id:
            raise serializers.ValidationError({"listing": "You cannot report your own listing."})
        if self.instance is None and listing.is_demo:
            raise serializers.ValidationError({"listing": "Demo listings cannot be reported."})
        if self.instance is None and ListingReport.objects.filter(
            listing=listing,
            reporter=request.user,
        ).exists():
            raise serializers.ValidationError({"listing": "You already reported this listing."})
        return attrs

    def get_listing_title(self, obj: Any) -> str:
        return obj.listing.title


class RescueRequestSerializer(serializers.ModelSerializer):
    seeker = UserSerializer(read_only=True)
    matched_listing_detail = ListingSerializer(source="matched_listing", read_only=True)
    can_edit = serializers.SerializerMethodField()
    can_match = serializers.SerializerMethodField()
    is_urgent = serializers.SerializerMethodField()
    time_left_label = serializers.SerializerMethodField()

    class Meta:
        model = RescueRequest
        fields = (
            "id",
            "seeker",
            "matched_listing",
            "matched_listing_detail",
            "title",
            "description",
            "category",
            "pickup_zone",
            "needed_by",
            "budget_amount",
            "urgency",
            "status",
            "can_edit",
            "can_match",
            "is_urgent",
            "time_left_label",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "seeker",
            "matched_listing",
            "matched_listing_detail",
            "can_edit",
            "can_match",
            "is_urgent",
            "time_left_label",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        needed_by = attrs.get("needed_by", getattr(self.instance, "needed_by", None))
        budget_amount = attrs.get(
            "budget_amount",
            getattr(self.instance, "budget_amount", Decimal("0.00")),
        )
        if needed_by and needed_by <= timezone.now():
            raise serializers.ValidationError({"needed_by": "Needed-by time must be in the future."})
        if Decimal(budget_amount) < Decimal("0.00"):
            raise serializers.ValidationError({"budget_amount": "Budget cannot be negative."})
        return attrs

    def update(self, instance: RescueRequest, validated_data: dict[str, Any]) -> RescueRequest:
        next_status = validated_data.get("status", instance.status)
        if next_status == RescueRequest.Status.OPEN:
            instance.matched_listing = None
        if next_status in {RescueRequest.Status.FULFILLED, RescueRequest.Status.CLOSED}:
            validated_data.setdefault("matched_listing", instance.matched_listing)
        return super().update(instance, validated_data)

    def get_can_edit(self, obj: Any) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.is_authenticated and obj.seeker_id == request.user.id)

    def get_can_match(self, obj: Any) -> bool:
        request = self.context.get("request")
        return bool(
            request
            and request.user.is_authenticated
            and obj.seeker_id != request.user.id
            and obj.status == RescueRequest.Status.OPEN
        )

    def get_is_urgent(self, obj: Any) -> bool:
        return obj.is_urgent

    def get_time_left_label(self, obj: Any) -> str:
        delta = obj.needed_by - timezone.now()
        hours_left = int(delta.total_seconds() // 3600)
        if hours_left <= 0:
            return "Need-by window closed"
        if hours_left < 24:
            return f"{hours_left}h left"
        days_left = hours_left // 24
        return f"{days_left}d left"


class HandoffFeedbackSerializer(serializers.ModelSerializer):
    reviewer = UserSerializer(read_only=True)
    reviewee = UserSerializer(read_only=True)
    listing_title = serializers.SerializerMethodField()

    class Meta:
        model = HandoffFeedback
        fields = (
            "id",
            "reservation",
            "listing_title",
            "reviewer",
            "reviewee",
            "rating",
            "tags",
            "note",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "reservation",
            "listing_title",
            "reviewer",
            "reviewee",
            "created_at",
            "updated_at",
        )

    def validate_tags(self, value: list[Any]) -> list[Any]:
        if not isinstance(value, list):
            raise serializers.ValidationError("Tags must be a list.")
        allowed = {tag for tag, _label in HandoffFeedback.Tag.choices}
        cleaned = []
        for tag in value:
            if tag not in allowed:
                raise serializers.ValidationError(f"'{tag}' is not a valid feedback tag.")
            if tag not in cleaned:
                cleaned.append(tag)
        if len(cleaned) > 3:
            raise serializers.ValidationError("Choose up to three tags.")
        return cleaned

    def validate_note(self, value: str) -> str:
        return value.strip()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        request = self.context["request"]
        reservation = self.context["reservation"]

        if reservation.status != Reservation.Status.COMPLETED:
            raise serializers.ValidationError(
                {"reservation": "Feedback is available only after the handoff is completed."}
            )

        is_owner = reservation.listing.owner_id == request.user.id
        is_claimant = reservation.claimant_id == request.user.id
        if not (is_owner or is_claimant):
            raise serializers.ValidationError(
                {"reservation": "Only people in this handoff can leave feedback."}
            )

        if self.instance is None and reservation.feedback_entries.filter(reviewer=request.user).exists():
            raise serializers.ValidationError(
                {"reservation": "You already left feedback for this handoff."}
            )
        return attrs

    def create(self, validated_data: dict[str, Any]) -> HandoffFeedback:
        request = self.context["request"]
        reservation = self.context["reservation"]
        reviewee = (
            reservation.claimant
            if reservation.listing.owner_id == request.user.id
            else reservation.listing.owner
        )
        return HandoffFeedback.objects.create(
            reservation=reservation,
            reviewer=request.user,
            reviewee=reviewee,
            **validated_data,
        )

    def get_listing_title(self, obj: Any) -> str:
        return obj.reservation.listing.title


class ReservationSerializer(serializers.ModelSerializer):
    claimant = UserSerializer(read_only=True)
    listing_detail = ListingSerializer(source="listing", read_only=True)
    relationship = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    handoff_code = serializers.SerializerMethodField()
    pickup_zone = serializers.SerializerMethodField()
    move_out_deadline = serializers.SerializerMethodField()
    counterparty_name = serializers.SerializerMethodField()
    time_pressure = serializers.SerializerMethodField()
    next_step = serializers.SerializerMethodField()
    handoff_checklist = serializers.SerializerMethodField()
    feedback_entries = serializers.SerializerMethodField()
    my_feedback = serializers.SerializerMethodField()
    can_leave_feedback = serializers.SerializerMethodField()
    feedback_summary = serializers.SerializerMethodField()
    handoff_pin_display = serializers.SerializerMethodField()
    unread_messages = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = (
            "id",
            "listing",
            "listing_detail",
            "claimant",
            "status",
            "pickup_time_window",
            "pickup_slots",
            "confirmed_slot",
            "relationship",
            "allowed_actions",
            "handoff_code",
            "handoff_pin_display",
            "pickup_zone",
            "move_out_deadline",
            "counterparty_name",
            "time_pressure",
            "next_step",
            "handoff_checklist",
            "feedback_entries",
            "my_feedback",
            "can_leave_feedback",
            "feedback_summary",
            "unread_messages",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "claimant",
            "pickup_slots",
            "confirmed_slot",
            "relationship",
            "allowed_actions",
            "handoff_code",
            "handoff_pin_display",
            "pickup_zone",
            "move_out_deadline",
            "counterparty_name",
            "time_pressure",
            "next_step",
            "handoff_checklist",
            "feedback_entries",
            "my_feedback",
            "can_leave_feedback",
            "feedback_summary",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        request = self.context["request"]

        if self.instance is None:
            listing = attrs["listing"]
            listing.refresh_status()

            if listing.is_demo:
                raise serializers.ValidationError({"listing": "Preview listings cannot be reserved."})
            if listing.owner_id == request.user.id:
                raise serializers.ValidationError({"listing": "You cannot reserve your own listing."})
            if listing.status != Listing.Status.AVAILABLE:
                raise serializers.ValidationError({"listing": "This listing is no longer available."})
            if listing.reservations.filter(
                status__in=[Reservation.Status.REQUESTED, Reservation.Status.CONFIRMED]
            ).exists():
                raise serializers.ValidationError(
                    {"listing": "Someone already has an active claim on this listing."}
                )
            if not attrs.get("pickup_time_window"):
                raise serializers.ValidationError(
                    {"pickup_time_window": "Add a pickup window so the owner can plan the handoff."}
                )
            attrs["status"] = Reservation.Status.REQUESTED
            return attrs

        next_status = attrs.get("status", self.instance.status)
        if next_status != self.instance.status:
            allowed = self._allowed_statuses(request.user, self.instance)
            if next_status not in allowed:
                raise serializers.ValidationError({"status": "That status change is not allowed."})

        return attrs

    def create(self, validated_data: dict[str, Any]) -> Reservation:
        listing = validated_data["listing"]
        reservation = Reservation.objects.create(**validated_data)
        listing.status = Listing.Status.RESERVED
        listing.save(update_fields=["status", "updated_at"])
        return reservation

    def update(self, instance: Reservation, validated_data: dict[str, Any]) -> Reservation:
        previous_status = instance.status
        reservation = super().update(instance, validated_data)

        if reservation.status != previous_status:
            listing = reservation.listing
            listing.refresh_status()
            if reservation.status in [Reservation.Status.REQUESTED, Reservation.Status.CONFIRMED]:
                listing.status = Listing.Status.RESERVED
            elif reservation.status == Reservation.Status.CANCELLED:
                RescueRequest.objects.filter(
                    matched_listing=listing,
                    status=RescueRequest.Status.MATCHED,
                ).update(
                    matched_listing=None,
                    status=RescueRequest.Status.OPEN,
                    updated_at=timezone.now(),
                )
                listing.status = (
                    Listing.Status.EXPIRED
                    if listing.available_until < timezone.now()
                    else Listing.Status.AVAILABLE
                )
                # Trust strike: owner cancelled after confirming (bad actor signal)
                if previous_status == Reservation.Status.CONFIRMED:
                    from django.db.models import F
                    from django.contrib.auth import get_user_model
                    get_user_model().objects.filter(pk=listing.owner_id).update(
                        trust_strikes=F("trust_strikes") + 1
                    )
            elif reservation.status == Reservation.Status.COMPLETED:
                RescueRequest.objects.filter(
                    matched_listing=listing,
                    status=RescueRequest.Status.MATCHED,
                ).update(
                    status=RescueRequest.Status.FULFILLED,
                    updated_at=timezone.now(),
                )
                listing.status = Listing.Status.PICKED_UP
            listing.save(update_fields=["status", "updated_at"])

            if reservation.status == Reservation.Status.CONFIRMED:
                # Generate a 6-digit handoff PIN shown to the claimant at pickup
                if not reservation.handoff_pin:
                    import secrets as _secrets
                    pin = str(_secrets.randbelow(1000000)).zfill(6)
                    Reservation.objects.filter(pk=reservation.pk).update(handoff_pin=pin)
                    reservation.handoff_pin = pin
                send_reservation_confirmed_email_task.delay(reservation.pk)
            elif reservation.status == Reservation.Status.COMPLETED:
                send_reservation_completed_email_task.delay(reservation.pk)

        return reservation

    def get_relationship(self, obj: Any) -> str:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return "viewer"
        return "owner" if obj.listing.owner_id == request.user.id else "claimant"

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_allowed_actions(self, obj: Reservation) -> list[Any]:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return []
        return self._allowed_statuses(request.user, obj)

    def _allowed_statuses(self, user: Any, reservation: Reservation) -> list[Any]:
        if reservation.status in [Reservation.Status.CANCELLED, Reservation.Status.COMPLETED]:
            return []

        is_owner = reservation.listing.owner_id == user.id
        is_claimant = reservation.claimant_id == user.id

        if reservation.status == Reservation.Status.REQUESTED:
            actions = []
            if is_owner:
                actions.append(Reservation.Status.CONFIRMED)
                actions.append(Reservation.Status.CANCELLED)
            if is_claimant:
                actions.append(Reservation.Status.CANCELLED)
            return actions

        if reservation.status == Reservation.Status.CONFIRMED:
            actions = []
            if is_owner:
                actions.append(Reservation.Status.COMPLETED)
                actions.append(Reservation.Status.CANCELLED)
            if is_claimant:
                actions.append(Reservation.Status.CANCELLED)
            return actions

        return []

    def get_handoff_code(self, obj: Any) -> str:
        return f"DC-{obj.listing_id:03d}-{obj.id:04d}"

    def get_handoff_pin_display(self, obj: Any) -> str | None:
        """Return the PIN only to the claimant — the owner never sees it."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        if obj.claimant_id == request.user.id and obj.status == Reservation.Status.CONFIRMED:
            return obj.handoff_pin or None
        return None

    def get_unread_messages(self, obj: Reservation) -> int:
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return 0
        return obj.messages.exclude(sender=request.user).filter(read_at__isnull=True).count()

    def get_pickup_zone(self, obj: Any) -> str | None:
        return obj.listing.pickup_zone

    def get_move_out_deadline(self, obj: Any) -> str | None:
        return obj.listing.available_until

    def get_counterparty_name(self, obj: Any) -> str:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return obj.claimant.display_name
        if obj.listing.owner_id == request.user.id:
            return obj.claimant.display_name
        return obj.listing.owner.display_name

    def get_time_pressure(self, obj: Any) -> str:
        remaining = obj.listing.available_until - timezone.now()
        hours_left = remaining.total_seconds() / 3600
        if hours_left <= 12:
            return "urgent"
        if hours_left <= 48:
            return "soon"
        return "normal"

    def get_next_step(self, obj: Any) -> str:
        request = self.context.get("request")
        relationship = self.get_relationship(obj) if request else "viewer"

        if obj.status == Reservation.Status.REQUESTED:
            if relationship == "owner":
                return "Review the pickup window and confirm or cancel the handoff."
            if relationship == "claimant":
                return "Wait for the owner to confirm your pickup window."
            return "This handoff is waiting on owner confirmation."

        if obj.status == Reservation.Status.CONFIRMED:
            if relationship == "owner":
                return "Meet the claimant at the pickup zone, then mark the handoff completed."
            if relationship == "claimant":
                return "Bring the handoff code and arrive during the confirmed pickup window."
            return "This handoff is confirmed and ready for pickup."

        if obj.status == Reservation.Status.COMPLETED:
            return "This handoff is complete."

        if obj.status == Reservation.Status.CANCELLED:
            return "This handoff was cancelled."

        return "Check the reservation status and coordinate the next step."

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_handoff_checklist(self, obj: Reservation) -> list[str]:
        deadline_line = (
            "Complete the handoff before the deadline hits."
            if self.get_time_pressure(obj) == "urgent"
            else "Complete the handoff before the move-out deadline closes."
        )
        base = [
            f"Meet at {obj.listing.pickup_zone}.",
            f"Use the pickup window: {obj.pickup_time_window}.",
            deadline_line,
        ]
        if obj.status == Reservation.Status.REQUESTED:
            return base + [
                "Confirm the reservation before making the trip.",
                "Keep an eye on listing updates for coordination notes.",
            ]
        if obj.status == Reservation.Status.CONFIRMED:
            return base + [
                f"Bring or share handoff code {self.get_handoff_code(obj)} at pickup.",
                "Mark the reservation complete once the item changes hands.",
            ]
        return base

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_feedback_entries(self, obj: Reservation) -> list[Any]:
        entries = obj.feedback_entries.select_related("reviewer", "reviewee", "reservation__listing")
        return list(HandoffFeedbackSerializer(entries, many=True, context=self.context).data)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_my_feedback(self, obj: Reservation) -> dict[str, Any] | None:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        feedback = obj.feedback_entries.filter(reviewer=request.user).select_related(
            "reviewer",
            "reviewee",
            "reservation__listing",
        ).first()
        if not feedback:
            return None
        return HandoffFeedbackSerializer(feedback, context=self.context).data

    def get_can_leave_feedback(self, obj: Any) -> bool:
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        if obj.status != Reservation.Status.COMPLETED:
            return False
        if request.user.id not in {obj.listing.owner_id, obj.claimant_id}:
            return False
        return not obj.feedback_entries.filter(reviewer=request.user).exists()

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_feedback_summary(self, obj: Reservation) -> dict[str, Any]:
        entries = list(obj.feedback_entries.all())
        if not entries:
            return {"count": 0, "average_rating": None}
        average = sum(entry.rating for entry in entries) / len(entries)
        return {"count": len(entries), "average_rating": round(average, 1)}


class ListingUpdateSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)

    class Meta:
        model = ListingUpdate
        fields = ("id", "listing", "author", "body", "created_at")
        read_only_fields = ("id", "listing", "author", "created_at")

    def validate_body(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Write a short update before posting.")
        return value


class SavedListingSerializer(serializers.ModelSerializer):
    listing = ListingSerializer(read_only=True)

    class Meta:
        model = SavedListing
        fields = ("id", "listing", "created_at")
        read_only_fields = fields


class ReservationMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = ReservationMessage
        fields = ("id", "sender_name", "is_mine", "body", "created_at")
        read_only_fields = ("id", "sender_name", "is_mine", "created_at")

    def get_sender_name(self, obj: ReservationMessage) -> str:
        return obj.sender.get_full_name() or obj.sender.email.split("@")[0]

    def get_is_mine(self, obj: ReservationMessage) -> bool:
        request = self.context.get("request")
        return request is not None and obj.sender_id == request.user.id
