from datetime import timedelta

from django.contrib import admin
from django.utils import timezone

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


class OverdueListingFilter(admin.SimpleListFilter):
    title = "overdue"
    parameter_name = "overdue"

    def lookups(self, request, model_admin):
        return [("yes", "Overdue"), ("no", "Not overdue")]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "yes":
            return queryset.filter(
                status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
                available_until__lt=now,
            )
        if self.value() == "no":
            return queryset.exclude(
                status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
                available_until__lt=now,
            )
        return queryset


class OverdueTaskFilter(admin.SimpleListFilter):
    title = "task urgency"
    parameter_name = "task_urgency"

    def lookups(self, request, model_admin):
        return [("overdue", "Overdue"), ("due_today", "Due Today")]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "overdue":
            return queryset.exclude(status=MoveOutTask.Status.DONE).filter(due_at__lt=now)
        if self.value() == "due_today":
            return queryset.exclude(status=MoveOutTask.Status.DONE).filter(
                due_at__gte=now,
                due_at__lte=now + timedelta(hours=24),
            )
        return queryset


class UnreadNotificationFilter(admin.SimpleListFilter):
    title = "read state"
    parameter_name = "read_state"

    def lookups(self, request, model_admin):
        return [("unread", "Unread"), ("read", "Read")]

    def queryset(self, request, queryset):
        if self.value() == "unread":
            return queryset.filter(is_read=False)
        if self.value() == "read":
            return queryset.filter(is_read=True)
        return queryset


@admin.action(description="Expire selected listings")
def expire_selected_listings(modeladmin, request, queryset):
    queryset.filter(
        status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED]
    ).update(status=Listing.Status.EXPIRED)


@admin.action(description="Mark selected tasks complete")
def mark_tasks_complete(modeladmin, request, queryset):
    queryset.update(status=MoveOutTask.Status.DONE, completed_at=timezone.now())


@admin.action(description="Mark selected notifications read")
def mark_notifications_read(modeladmin, request, queryset):
    queryset.update(is_read=True, read_at=timezone.now())


@admin.action(description="Mark selected reports reviewing")
def mark_reports_reviewing(modeladmin, request, queryset):
    queryset.update(status=ListingReport.Status.REVIEWING, reviewed_at=timezone.now())


@admin.action(description="Mark selected reports resolved")
def mark_reports_resolved(modeladmin, request, queryset):
    queryset.update(status=ListingReport.Status.RESOLVED, reviewed_at=timezone.now())


@admin.action(description="Dismiss selected reports")
def mark_reports_dismissed(modeladmin, request, queryset):
    queryset.update(status=ListingReport.Status.DISMISSED, reviewed_at=timezone.now())


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "status", "moderation_status", "price_type", "available_until", "source_scan_session", "is_demo")
    list_filter = ("status", "moderation_status", "price_type", "category", "is_demo", OverdueListingFilter)
    search_fields = ("title", "description", "pickup_zone", "owner__email")
    readonly_fields = ("moderation_flag_reason",)
    actions = [expire_selected_listings]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("listing", "claimant", "status", "pickup_time_window", "updated_at")
    list_filter = ("status",)
    search_fields = ("listing__title", "claimant__email")


@admin.register(RescueRequest)
class RescueRequestAdmin(admin.ModelAdmin):
    list_display = ("title", "seeker", "category", "status", "urgency", "needed_by", "matched_listing")
    list_filter = ("status", "urgency", "category")
    search_fields = ("title", "description", "pickup_zone", "seeker__email")


@admin.register(SavedListing)
class SavedListingAdmin(admin.ModelAdmin):
    list_display = ("user", "listing", "created_at")
    search_fields = ("user__email", "listing__title")


@admin.register(ListingUpdate)
class ListingUpdateAdmin(admin.ModelAdmin):
    list_display = ("listing", "author", "created_at")
    search_fields = ("listing__title", "author__email", "body")


@admin.register(ListingReport)
class ListingReportAdmin(admin.ModelAdmin):
    list_display = ("listing", "reporter", "reason", "status", "created_at", "reviewed_at")
    list_filter = ("reason", "status")
    search_fields = ("listing__title", "reporter__email", "details")
    actions = [mark_reports_reviewing, mark_reports_resolved, mark_reports_dismissed]


@admin.register(RoomScanSession)
class RoomScanSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "room_label", "room_type", "status", "progress_percent", "is_demo")
    list_filter = ("status", "room_type", "is_demo")
    search_fields = ("name", "room_label", "pickup_zone", "owner__email")


@admin.register(RoomScanImage)
class RoomScanImageAdmin(admin.ModelAdmin):
    list_display = ("scan_session", "position", "created_at")
    search_fields = ("scan_session__name",)


@admin.register(RoomScanItemDraft)
class RoomScanItemDraftAdmin(admin.ModelAdmin):
    list_display = ("title", "scan_session", "triage_status", "linked_listing", "donation_hub", "updated_at")
    list_filter = ("triage_status", "category", "condition", "price_type")
    search_fields = ("title", "notes", "scan_session__name")


@admin.register(MoveOutTask)
class MoveOutTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "scan_session", "status", "due_at", "is_system", "completed_at")
    list_filter = ("status", "category", "is_system", OverdueTaskFilter)
    search_fields = ("title", "details", "scan_session__name")
    actions = [mark_tasks_complete]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "type", "priority", "is_read", "created_at")
    list_filter = ("type", "priority", UnreadNotificationFilter)
    search_fields = ("title", "body", "user__email", "link_path")
    actions = [mark_notifications_read]


@admin.register(ReservationMessage)
class ReservationMessageAdmin(admin.ModelAdmin):
    list_display = ("reservation", "sender", "body", "created_at")
    search_fields = ("body", "sender__email", "reservation__listing__title")
    raw_id_fields = ("reservation", "sender")


@admin.register(HandoffFeedback)
class HandoffFeedbackAdmin(admin.ModelAdmin):
    list_display = ("reservation", "reviewer", "reviewee", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = (
        "reservation__listing__title",
        "reviewer__email",
        "reviewee__email",
        "note",
    )
