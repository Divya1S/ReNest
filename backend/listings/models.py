import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from typing import Any


class ListingManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Listing(models.Model):
    class Category(models.TextChoices):
        STORAGE = "storage", "Storage"
        LIGHTING = "lighting", "Lighting"
        SUPPLIES = "supplies", "School Supplies"
        COMFORT = "comfort", "Comfort"
        TOILETRIES = "toiletries", "Toiletries"
        DECOR = "decor", "Decor"
        OTHER = "other", "Other"

    class Condition(models.TextChoices):
        NEW = "new", "Like New"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"

    class PriceType(models.TextChoices):
        FREE = "free", "Free"
        LOW_COST = "low_cost", "Low Cost"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        PICKED_UP = "picked_up", "Picked Up"
        EXPIRED = "expired", "Expired"
        DONATED = "donated", "Donated"

    class ModerationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        FLAGGED = "flagged", "Flagged"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    source_scan_session = models.ForeignKey(
        "RoomScanSession",
        on_delete=models.SET_NULL,
        related_name="generated_listings",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=140)
    description = models.TextField()
    category = models.CharField(max_length=24, choices=Category.choices)
    condition = models.CharField(max_length=24, choices=Condition.choices)
    price_type = models.CharField(max_length=24, choices=PriceType.choices)
    price_amount = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    estimated_retail_value = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    pickup_zone = models.CharField(max_length=140)
    available_until = models.DateTimeField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.AVAILABLE)
    image = models.ImageField(upload_to="listing-images/", blank=True, null=True)
    image_thumb = models.ImageField(
        upload_to="listing-thumbs/",
        blank=True,
        null=True,
        editable=False,
        help_text="Auto-generated small rendition of `image` for card grids.",
    )
    image_cdn_url = models.URLField(max_length=500, blank=True, default="")
    is_demo = models.BooleanField(default=False)
    repost_token = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text="Short-lived signed token for one-click relist from an expiry notification.",
    )
    repost_token_expires_at = models.DateTimeField(null=True, blank=True)
    building = models.CharField(
        max_length=120,
        blank=True,
        help_text="Dorm or building name for proximity filtering.",
    )
    moderation_status = models.CharField(
        max_length=16,
        choices=ModerationStatus.choices,
        default=ModerationStatus.PENDING,
        db_index=True,
    )
    moderation_flag_reason = models.TextField(blank=True)
    condition_verified = models.BooleanField(
        default=False,
        help_text="Set to True when a HandoffFeedback from the claimant confirms condition accuracy.",
    )
    bump_emailed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time a 'still available?' re-engagement email was sent to the owner.",
    )
    boosted_until = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Phase 17: listings boosted via Stripe tip surface first in browse until this datetime.",
    )
    # Phase 25 — Semantic Search
    embedding = models.JSONField(
        null=True,
        blank=True,
        help_text="HashingVectorizer feature vector for semantic search similarity ranking.",
    )
    # Phase 18 — Platform Intelligence
    quality_hints = models.JSONField(
        default=list,
        blank=True,
        help_text="AI-generated suggestions to improve listing quality. Shape: [{suggestion, field}].",
    )
    quality_hints_dismissed = models.BooleanField(default=False)
    donation_hub = models.ForeignKey(
        "hubs.DonationHub",
        on_delete=models.SET_NULL,
        related_name="donated_listings",
        null=True,
        blank=True,
    )
    donation_receipt = models.FileField(
        upload_to="donation-receipts/",
        null=True,
        blank=True,
    )
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ListingManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ("available_until", "-created_at")
        indexes = [
            # Browse active listings (default sort + status filter)
            models.Index(fields=["is_demo", "status", "available_until"], name="listing_active_browse_idx"),
            # "My listings" view (owner-scoped browse)
            models.Index(fields=["owner", "is_demo", "available_until"], name="listing_owner_browse_idx"),
            # Category + status filter (browse page filters + insights market map)
            models.Index(fields=["category", "status", "available_until"], name="listing_category_filter_idx"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_urgent(self):
        return self.status in {self.Status.AVAILABLE, self.Status.RESERVED} and (
            self.available_until <= timezone.now() + timedelta(days=3)
        )

    def delete(self, using=None, keep_parents=False):  # noqa: ARG002
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)

    def refresh_status(self, commit=True):
        if self.status in {self.Status.AVAILABLE, self.Status.RESERVED} and self.available_until < timezone.now():
            newly_expired = self.status != self.Status.EXPIRED
            self.status = self.Status.EXPIRED
            if newly_expired and not self.repost_token:
                self.repost_token = secrets.token_urlsafe(32)
                self.repost_token_expires_at = timezone.now() + timedelta(days=7)
            if commit:
                self.save(update_fields=["status", "repost_token", "repost_token_expires_at", "updated_at"])
        return self.status


class ListingImage(models.Model):
    """Additional gallery photo for a listing. Listing.image stays the cover."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="listing-images/")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")

    def __str__(self) -> str:
        return f"Gallery image {self.pk} for listing {self.listing_id}"


class ListingViewEvent(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="view_events")
    session_key = models.CharField(max_length=64, db_index=True)
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("listing", "session_key"),)

    def __str__(self) -> str:
        return f"View of listing {self.listing_id} by session {self.session_key[:8]}…"


class RescueRequest(models.Model):
    class Urgency(models.TextChoices):
        FLEXIBLE = "flexible", "Flexible"
        SOON = "soon", "Soon"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MATCHED = "matched", "Matched"
        FULFILLED = "fulfilled", "Fulfilled"
        CLOSED = "closed", "Closed"

    seeker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rescue_requests",
    )
    matched_listing = models.ForeignKey(
        "Listing",
        on_delete=models.SET_NULL,
        related_name="matched_rescue_requests",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=140)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=24, choices=Listing.Category.choices)
    pickup_zone = models.CharField(max_length=140)
    needed_by = models.DateTimeField()
    budget_amount = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    urgency = models.CharField(
        max_length=16,
        choices=Urgency.choices,
        default=Urgency.FLEXIBLE,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("needed_by", "-created_at")
        indexes = [
            # Open requests sorted by urgency (match center + insights)
            models.Index(fields=["status", "needed_by"], name="rescuerequest_open_sort_idx"),
            # My requests by status (dashboard + profile)
            models.Index(fields=["seeker", "status"], name="rescuereq_seeker_status_idx"),
            # Category filter for match center fulfill opportunities
            models.Index(fields=["category", "status", "needed_by"], name="rescuerequest_match_center_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.seeker})"

    @property
    def is_urgent(self):
        return self.status in {self.Status.OPEN, self.Status.MATCHED} and (
            self.needed_by <= timezone.now() + timedelta(days=2)
            or self.urgency == self.Urgency.URGENT
        )


class RoomScanSession(models.Model):
    class Status(models.TextChoices):
        SETUP = "setup", "Setup"
        SCANNING = "scanning", "Scanning"
        CLEAROUT = "clearout", "Clear-Out"
        COMPLETED = "completed", "Completed"

    class RoomType(models.TextChoices):
        DORM_ROOM = "dorm_room", "Dorm Room"
        SUITE = "suite", "Suite"
        APARTMENT = "apartment", "Apartment"
        BATHROOM = "bathroom", "Bathroom"
        STORAGE_CORNER = "storage_corner", "Storage Corner"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="room_scan_sessions",
    )
    name = models.CharField(max_length=140)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SETUP)
    room_label = models.CharField(max_length=140)
    room_type = models.CharField(max_length=24, choices=RoomType.choices, default=RoomType.DORM_ROOM)
    pickup_zone = models.CharField(max_length=140, blank=True)
    move_out_deadline = models.DateTimeField(null=True, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    is_demo = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self):
        return self.name

    def refresh_progress(self, commit=True):
        items = RoomScanItemDraft.objects.filter(scan_session=self)
        images = RoomScanImage.objects.filter(scan_session=self)
        total = items.count()
        handled = items.filter(
            triage_status__in=[
                RoomScanItemDraft.TriageStatus.KEEP,
                RoomScanItemDraft.TriageStatus.TOSS,
                RoomScanItemDraft.TriageStatus.DONE,
            ]
        ).count()
        self.progress_percent = 0 if total == 0 else round((handled / total) * 100)

        if total == 0:
            self.status = self.Status.SCANNING if images.exists() else self.Status.SETUP
        elif items.exclude(
            triage_status__in=[
                RoomScanItemDraft.TriageStatus.KEEP,
                RoomScanItemDraft.TriageStatus.TOSS,
                RoomScanItemDraft.TriageStatus.DONE,
            ]
        ).exists():
            self.status = self.Status.CLEAROUT
        else:
            self.status = self.Status.COMPLETED

        if commit:
            self.save(update_fields=["progress_percent", "status", "updated_at"])
        return self.progress_percent


class MoveOutTask(models.Model):
    class Category(models.TextChoices):
        SETUP = "setup", "Setup"
        PUBLISH = "publish", "Publish"
        DONATION = "donation", "Donation"
        PICKUP = "pickup", "Pickup"
        ROOM_RESET = "room_reset", "Room Reset"
        ADMIN = "admin", "Admin"

    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

    scan_session = models.ForeignKey(
        RoomScanSession,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=160)
    details = models.TextField(blank=True)
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.SETUP)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.TODO)
    due_at = models.DateTimeField(null=True, blank=True)
    is_system = models.BooleanField(default=False)
    system_key = models.CharField(max_length=32, null=True, blank=True)
    offset_hours = models.IntegerField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("completed_at", "due_at", "created_at")
        indexes = [
            # Critical tasks query: exclude DONE, filter by due_at (dashboard + scan plan)
            models.Index(fields=["scan_session", "status", "due_at"], name="task_session_status_due_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.scan_session.name})"

    @property
    def is_overdue(self):
        return bool(
            self.status != self.Status.DONE
            and self.due_at
            and self.due_at < timezone.now()
        )

    def sync_due_at_from_session(self, commit=True):
        if self.offset_hours is None:
            return self.due_at
        self.due_at = (
            self.scan_session.move_out_deadline - timedelta(hours=self.offset_hours)
            if self.scan_session.move_out_deadline
            else None
        )
        if commit:
            self.save(update_fields=["due_at", "updated_at"])
        return self.due_at


class RoomScanImage(models.Model):
    scan_session = models.ForeignKey(
        RoomScanSession,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="scan-images/")
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "created_at")

    def __str__(self):
        return f"{self.scan_session.name} image {self.position}"


class RoomScanItemDraft(models.Model):
    class TriageStatus(models.TextChoices):
        REVIEW = "review", "Review"
        KEEP = "keep", "Keep"
        SELL = "sell", "Sell"
        DONATE = "donate", "Donate"
        TOSS = "toss", "Toss"
        DONE = "done", "Done"

    scan_session = models.ForeignKey(
        RoomScanSession,
        on_delete=models.CASCADE,
        related_name="items",
    )
    source_image = models.ForeignKey(
        RoomScanImage,
        on_delete=models.SET_NULL,
        related_name="draft_items",
        null=True,
        blank=True,
    )
    donation_hub = models.ForeignKey(
        "hubs.DonationHub",
        on_delete=models.SET_NULL,
        related_name="scan_donation_items",
        null=True,
        blank=True,
    )
    hotspot_box = models.JSONField(default=dict)
    title = models.CharField(max_length=140)
    category = models.CharField(max_length=24, choices=Listing.Category.choices)
    condition = models.CharField(max_length=24, choices=Listing.Condition.choices)
    price_type = models.CharField(max_length=24, choices=Listing.PriceType.choices)
    price_amount = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    estimated_retail_value = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    preset_key = models.CharField(max_length=32, null=True, blank=True)
    triage_status = models.CharField(max_length=24, choices=TriageStatus.choices, default=TriageStatus.REVIEW)
    notes = models.TextField(blank=True)
    linked_listing = models.OneToOneField(
        Listing,
        on_delete=models.SET_NULL,
        related_name="scan_draft_source",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [
            # Publish queue + dashboard ready-to-publish filter
            models.Index(fields=["scan_session", "triage_status"], name="scanitem_session_triage_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.scan_session.name})"


class Reservation(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        EXPIRED_UNRESOLVED = "expired_unresolved", "Expired Unresolved"

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="reservations")
    claimant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.REQUESTED)
    pickup_time_window = models.CharField(max_length=140)
    # Phase 16 — Handoff Coordination
    pickup_slots = models.JSONField(
        default=list,
        blank=True,
        help_text="Up to 5 ISO-8601 datetime strings proposed by the owner.",
    )
    confirmed_slot = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The slot the claimant confirmed from pickup_slots.",
    )
    handoff_pin = models.CharField(
        max_length=6,
        blank=True,
        help_text="6-digit PIN shown to claimant; owner enters it at pickup to auto-complete.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            # Outgoing reservations by status (dashboard + profile)
            models.Index(fields=["claimant", "status"], name="reservation_claim_status_idx"),
            # Incoming reservations by status (incoming action queue)
            models.Index(fields=["listing", "status"], name="reservation_listing_status_idx"),
        ]

    def __str__(self):
        return f"{self.claimant} -> {self.listing}"


class SavedListing(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_listings",
    )
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="saved_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=("user", "listing"), name="unique_saved_listing"),
        ]

    def __str__(self):
        return f"{self.user} saved {self.listing}"


class ListingUpdate(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="updates")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listing_updates",
    )
    body = models.TextField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Update by {self.author} on {self.listing}"


class Notification(models.Model):
    class Type(models.TextChoices):
        READY_TO_PUBLISH = "ready_to_publish", "Ready To Publish"
        BLOCKED_SCAN = "blocked_scan", "Blocked Scan"
        RESERVATION = "reservation", "Reservation"
        MOVE_OUT_TASK = "move_out_task", "Move-Out Task"
        HANDOFF_URGENT = "handoff_urgent", "Urgent Handoff"
        SYSTEM = "system", "System"
        AI_MATCH = "ai_match", "AI Match"
        CHAT = "chat", "Chat Message"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    type = models.CharField(max_length=32, choices=Type.choices)
    title = models.CharField(max_length=160)
    body = models.TextField()
    link_path = models.CharField(max_length=240, blank=True)
    priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    dedupe_key = models.CharField(max_length=190, unique=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("is_read", "-created_at")
        indexes = [
            # Unread notification badge + notification list (user-scoped, unread-first)
            models.Index(fields=["user", "is_read", "created_at"], name="notification_user_unread_idx"),
        ]

    def __str__(self):
        return f"{self.user} • {self.title}"


class ListingReport(models.Model):
    class Reason(models.TextChoices):
        SAFETY = "safety", "Safety Concern"
        SPAM = "spam", "Spam or Scam"
        INACCURATE = "inaccurate", "Inaccurate Listing"
        NO_SHOW = "no_show", "Pickup / No-Show Issue"
        INAPPROPRIATE = "inappropriate", "Inappropriate Content"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Reviewing"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listing_reports",
    )
    reason = models.CharField(max_length=32, choices=Reason.choices)
    details = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("status", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("listing", "reporter"),
                name="unique_listing_report_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.reporter} reported {self.listing}"


class HandoffFeedback(models.Model):
    class Tag(models.TextChoices):
        RESPONSIVE = "responsive", "Responsive"
        ON_TIME = "on_time", "On Time"
        EASY_PICKUP = "easy_pickup", "Easy Pickup"
        FRIENDLY = "friendly", "Friendly"
        AS_DESCRIBED = "as_described", "As Described"
        GREAT_VALUE = "great_value", "Great Value"
        CLEAR_COMMUNICATION = "clear_communication", "Clear Communication"

    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.CASCADE,
        related_name="feedback_entries",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="handoff_feedback_given",
    )
    reviewee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="handoff_feedback_received",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    tags = models.JSONField(default=list, blank=True)
    note = models.TextField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("reservation", "reviewer"),
                name="unique_handoff_feedback_per_reviewer",
            ),
        ]

    def __str__(self):
        return f"{self.reviewer} rated {self.reviewee} for {self.reservation}"


class ListingInteractionEvent(models.Model):
    """
    Authenticated user interactions with listings — used for personalised re-ranking.
    Separate from the anonymous ListingViewEvent.
    """

    class Action(models.TextChoices):
        VIEW = "view", "View"
        SAVE = "save", "Save"
        RESERVE = "reserve", "Reserve"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listing_interactions",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="interactions",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "action", "created_at"], name="interaction_user_action_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.action} {self.listing_id}"


class BlockedUser(models.Model):
    """A user has blocked another user — hides their listings and prevents interaction."""

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocking",
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocked_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("blocker", "blocked"), name="unique_block"),
        ]
        indexes = [
            models.Index(fields=["blocker"], name="block_blocker_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.blocker} blocked {self.blocked}"


class Dispute(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    reservation = models.ForeignKey(
        "Reservation",
        on_delete=models.CASCADE,
        related_name="disputes",
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="opened_disputes",
    )
    reason = models.TextField(max_length=1000)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_disputes",
        null=True,
        blank=True,
    )
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "created_at"], name="dispute_status_date_idx"),
        ]

    def __str__(self) -> str:
        return f"Dispute #{self.pk} on reservation {self.reservation_id} ({self.status})"


class PushSubscription(models.Model):
    """Web Push API subscription for a user's browser/device.
    Phase 22: also holds native APNs/FCM tokens from the Capacitor shell.
    """

    class Platform(models.TextChoices):
        WEB = "web", "Web (VAPID)"
        APNS = "apns", "iOS (APNs)"
        FCM = "fcm", "Android (FCM)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    platform = models.CharField(
        max_length=8,
        choices=Platform.choices,
        default=Platform.WEB,
        db_index=True,
    )
    # Web VAPID fields (null for native tokens)
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField(blank=True)
    auth = models.TextField(blank=True)
    # Native token (APNs device token or FCM registration token)
    native_token = models.TextField(blank=True, db_index=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"], name="push_sub_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.endpoint[:60]}"


class DemandForecast(models.Model):
    """
    Phase 18 — weekly per-category demand forecast computed by a scikit-learn
    linear regression over the last 3 semesters of interaction data.
    Recomputed every Sunday midnight by a Celery beat task.
    """

    category = models.CharField(max_length=24, choices=Listing.Category.choices, db_index=True)
    week_number = models.PositiveSmallIntegerField(help_text="ISO week number (1–53) this forecast applies to.")
    year = models.PositiveSmallIntegerField()
    predicted_views = models.FloatField(default=0.0)
    predicted_saves = models.FloatField(default=0.0)
    is_peak = models.BooleanField(default=False, help_text="True when predicted_views >= 75th percentile for the category.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-year", "-week_number", "category")
        constraints = [
            models.UniqueConstraint(fields=("category", "week_number", "year"), name="unique_demand_forecast"),
        ]

    def __str__(self) -> str:
        return f"{self.category} w{self.week_number}/{self.year} — {self.predicted_views:.0f} views"


class Organization(models.Model):
    """
    Phase 17 — Tip Jar: campus sustainability clubs, dorm councils, and donation hubs
    can register here and receive optional tip allocations from listing completions.
    """

    name = models.CharField(max_length=200)
    cause_url = models.URLField(blank=True)
    campus_name = models.CharField(max_length=120, blank=True)
    venmo_handle = models.CharField(max_length=80, blank=True)
    verified = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class DonationIntent(models.Model):
    """
    Phase 17 — Honour-system tip logged when a listing completes.
    No actual payment processing — the owner's Venmo QR does the transfer.
    """

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="donation_intents",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="donation_intents",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="donation_intents",
    )
    percentage = models.PositiveSmallIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.owner} → {self.organization} ({self.percentage}%)"


class Announcement(models.Model):
    """Phase 21 — Campus-manager posts pinned to the browse/dashboard header."""

    campus = models.ForeignKey(
        "accounts.Campus",
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="announcements",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_pinned", "-created_at")

    def __str__(self) -> str:
        return f"[{self.campus}] {self.title}"


class ReservationMessage(models.Model):
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reservation_messages",
    )
    body = models.TextField(max_length=1000)
    # Set when the counterparty fetches the thread — reservation chat is
    # two-party, so a single timestamp fully captures "seen by the other side".
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        indexes = [
            models.Index(fields=["reservation", "created_at"], name="resmsg_reservation_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.sender} in reservation {self.reservation_id}"


DEFAULT_MOVE_OUT_TASKS: dict[str, list[dict[str, Any]]] = {
    RoomScanSession.RoomType.DORM_ROOM: [
        {
            "system_key": "lock_pickup_zone",
            "title": "Lock the pickup zone and building handoff rules",
            "details": "Make sure every listing and rescue draft inherits the right lobby, desk, or locker location.",
            "category": MoveOutTask.Category.SETUP,
            "offset_hours": 36,
        },
        {
            "system_key": "publish_best_items",
            "title": "Publish your best rescue items before the last-minute rush",
            "details": "Move the strongest items through the publish queue while there is still time for pickup coordination.",
            "category": MoveOutTask.Category.PUBLISH,
            "offset_hours": 24,
        },
        {
            "system_key": "route_donations",
            "title": "Route leftover toiletries and extras to a donation hub",
            "details": "Anything clean, sealed, or easy to reuse should be routed before the final toss sweep starts.",
            "category": MoveOutTask.Category.DONATION,
            "offset_hours": 18,
        },
        {
            "system_key": "confirm_pickups",
            "title": "Confirm reservation windows with anyone claiming your items",
            "details": "Resolve outstanding pickup messages so handoffs happen before your room closes out.",
            "category": MoveOutTask.Category.PICKUP,
            "offset_hours": 10,
        },
        {
            "system_key": "final_room_sweep",
            "title": "Do a final room sweep and wipe-down",
            "details": "Use the clear-out board one last time, then finish the physical reset before checkout.",
            "category": MoveOutTask.Category.ROOM_RESET,
            "offset_hours": 4,
        },
    ],
    RoomScanSession.RoomType.BATHROOM: [
        {
            "system_key": "bathroom_publish",
            "title": "Publish or donate sealed bathroom extras",
            "details": "Bathroom scans move quickly when toiletries and organizers are handled early.",
            "category": MoveOutTask.Category.DONATION,
            "offset_hours": 18,
        },
        {
            "system_key": "bathroom_reset",
            "title": "Clear counters and sanitize the shared bathroom area",
            "details": "Wrap the scan with a final bathroom reset so nothing reusable gets left behind.",
            "category": MoveOutTask.Category.ROOM_RESET,
            "offset_hours": 6,
        },
    ],
}


def build_default_move_out_tasks(scan_session):
    templates = list(DEFAULT_MOVE_OUT_TASKS[RoomScanSession.RoomType.DORM_ROOM])
    if scan_session.room_type == RoomScanSession.RoomType.BATHROOM:
        templates = DEFAULT_MOVE_OUT_TASKS[RoomScanSession.RoomType.BATHROOM] + templates[:3]
    elif scan_session.room_type == RoomScanSession.RoomType.STORAGE_CORNER:
        templates = templates[:4]
    elif scan_session.room_type in {
        RoomScanSession.RoomType.SUITE,
        RoomScanSession.RoomType.APARTMENT,
    }:
        templates = templates + [
            {
                "system_key": "roommate_sync",
                "title": "Sync with roommates on shared items and handoff timing",
                "details": "Make sure shared bins, decor, and common-area leftovers have a clear owner or destination.",
                "category": MoveOutTask.Category.ADMIN,
                "offset_hours": 12,
            }
        ]

    tasks = []
    for template in templates:
        task = MoveOutTask(
            scan_session=scan_session,
            title=template["title"],
            details=template["details"],
            category=template["category"],
            is_system=True,
            system_key=template["system_key"],
            offset_hours=template["offset_hours"],
        )
        task.sync_due_at_from_session(commit=False)
        tasks.append(task)
    return tasks


def seed_default_move_out_tasks(scan_session):
    if scan_session.tasks.exists():
        return scan_session.tasks.count()
    tasks = build_default_move_out_tasks(scan_session)
    MoveOutTask.objects.bulk_create(tasks)
    return len(tasks)


def sync_system_move_out_tasks(scan_session):
    tasks = list(scan_session.tasks.filter(is_system=True).exclude(offset_hours__isnull=True))
    for task in tasks:
        task.sync_due_at_from_session(commit=False)
    if tasks:
        MoveOutTask.objects.bulk_update(tasks, ["due_at"])
    return len(tasks)


class BuildingCoord(models.Model):
    """Phase 25 — lat/lon for a named building on a campus, used for map view and proximity sort."""

    campus = models.ForeignKey(
        "accounts.Campus",
        on_delete=models.CASCADE,
        related_name="building_coords",
    )
    building = models.CharField(max_length=120, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("campus", "building"),)
        ordering = ["campus", "building"]

    def __str__(self) -> str:
        return f"{self.building} @ {self.campus}"


class SavedSearch(models.Model):
    """Phase 25 — a persisted search preset that triggers push alerts when new matches appear."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_searches",
    )
    label = models.CharField(max_length=120, blank=True)
    category = models.CharField(max_length=24, blank=True)
    keyword = models.CharField(max_length=200, blank=True)
    price_type = models.CharField(max_length=16, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "category", "keyword", "price_type"),
                name="unique_saved_search",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.label or self.keyword or self.category}"


class StorageHold(models.Model):
    """
    Phase 24 — Logistics & Batching.
    A hub staff member places a temporary hold on a listing to batch it with
    a scheduled collection event, preventing it from being reserved elsewhere.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RELEASED = "released", "Released"
        COLLECTED = "collected", "Collected"

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="storage_holds",
    )
    held_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="placed_holds",
    )
    hub = models.ForeignKey(
        "hubs.DonationHub",
        on_delete=models.CASCADE,
        related_name="storage_holds",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    note = models.CharField(max_length=300, blank=True)
    expires_at = models.DateTimeField(help_text="Hold auto-releases at this time if not collected.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["hub", "status"], name="hold_hub_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Hold on listing {self.listing_id} at {self.hub}"


class CollectionEvent(models.Model):
    """
    Phase 24 — Logistics & Batching.
    A scheduled hub collection run: a van/person picks up multiple held listings
    from dorms in a defined route window.
    """

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    hub = models.ForeignKey(
        "hubs.DonationHub",
        on_delete=models.CASCADE,
        related_name="collection_events",
    )
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="organized_collections",
    )
    title = models.CharField(max_length=200)
    scheduled_at = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SCHEDULED,
        db_index=True,
    )
    holds = models.ManyToManyField(
        StorageHold,
        related_name="collection_events",
        blank=True,
    )
    route_notes = models.TextField(blank=True, help_text="Optimised stop order or building sequence.")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-scheduled_at",)
        indexes = [
            models.Index(fields=["hub", "status", "scheduled_at"], name="collection_hub_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} @ {self.hub} ({self.scheduled_at:%Y-%m-%d})"


class NotificationPreference(models.Model):
    """
    Phase 26 — per-user, per-type notification delivery preferences.
    Quiet hours are stored as naive Time values interpreted in the user's campus timezone.
    """

    class Channel(models.TextChoices):
        BOTH = "both", "Push + Email"
        PUSH = "push", "Push only"
        EMAIL = "email", "Email only"
        OFF = "off", "Off"

    class NotificationType(models.TextChoices):
        RESERVATION_CONFIRMED = "reservation", "Reservation Confirmed"
        HANDOFF_REMINDER = "handoff_urgent", "Handoff Reminder"
        MATCH_ALERT = "ai_match", "Match Alert"
        LISTING_EXPIRED = "system", "Listing Expired"
        WEEKLY_DIGEST = "weekly_digest", "Weekly Digest"
        ANNOUNCEMENTS = "ready_to_publish", "Announcements"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    notification_type = models.CharField(
        max_length=32,
        choices=NotificationType.choices,
        db_index=True,
    )
    channel = models.CharField(
        max_length=8,
        choices=Channel.choices,
        default=Channel.BOTH,
    )
    quiet_hours_start = models.TimeField(
        null=True,
        blank=True,
        help_text="Start of quiet window (local time). Null = no quiet hours.",
    )
    quiet_hours_end = models.TimeField(
        null=True,
        blank=True,
        help_text="End of quiet window (local time). Null = no quiet hours.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "notification_type"),)
        indexes = [
            models.Index(fields=["user", "notification_type"], name="notif_pref_user_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user} — {self.notification_type} ({self.channel})"


class PartnerAPIKey(models.Model):
    """
    Phase 27 — API key for partner integrations (housing portals, sustainability dashboards).
    The raw key is shown once at creation; only the SHA-256 hash is stored.
    """

    campus = models.ForeignKey(
        "accounts.Campus",
        on_delete=models.CASCADE,
        related_name="partner_api_keys",
    )
    partner_name = models.CharField(max_length=200)
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    scopes = models.JSONField(
        default=list,
        help_text="List of granted scopes: listing_read, reservation_read, webhook.",
    )
    rate_limit_per_day = models.PositiveIntegerField(default=1000)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["campus", "active"], name="partner_key_campus_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.partner_name} ({self.campus})"

    @staticmethod
    def hash_key(raw_key: str) -> str:
        import hashlib
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def generate(cls, *, campus: Any, partner_name: str, scopes: list) -> tuple["PartnerAPIKey", str]:
        """Create a new key; return (instance, raw_key). Raw key is never stored."""
        import secrets
        raw = f"dc_{secrets.token_urlsafe(32)}"
        instance = cls.objects.create(
            campus=campus,
            partner_name=partner_name,
            key_hash=cls.hash_key(raw),
            scopes=scopes,
        )
        return instance, raw


class WebhookEndpoint(models.Model):
    """Phase 27 — A partner-registered URL that receives signed event payloads."""

    partner = models.ForeignKey(
        PartnerAPIKey,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
    )
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=64, help_text="HMAC-SHA256 signing secret.")
    events = models.JSONField(
        default=list,
        help_text="List of event types: listing_created, reservation_confirmed, listing_donated.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["partner", "active"], name="webhook_ep_partner_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.url} ({self.partner.partner_name})"


class WebhookDelivery(models.Model):
    """Phase 27 — Delivery attempt log for a webhook event."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["endpoint", "status", "created_at"], name="webhook_del_ep_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} → {self.endpoint.url} ({self.status})"
