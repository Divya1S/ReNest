from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .ai_client import ai_available, generate_json
from .models import Listing, MoveOutTask, Notification, Reservation, RescueRequest, RoomScanSession
from .serializers import compute_scan_summary

if TYPE_CHECKING:
    from accounts.models import User as UserType

User = get_user_model()


def _notification_payload(
    *,
    type: str,
    title: str,
    body: str,
    link_path: str,
    priority: str,
    dedupe_key: str,
) -> dict[str, Any]:
    return {
        "type": type,
        "title": title,
        "body": body,
        "link_path": link_path,
        "priority": priority,
        "dedupe_key": dedupe_key,
    }


def build_notification_specs_for_user(user: UserType) -> list[dict[str, Any]]:
    now = timezone.now()
    specs: list[dict[str, Any]] = []

    scan_sessions = (
        RoomScanSession.objects.filter(owner=user, is_demo=False)
        .prefetch_related("items__linked_listing", "generated_listings", "tasks")
        .order_by("-updated_at")
    )
    for session in scan_sessions:
        summary = compute_scan_summary(session)
        if summary["ready_to_publish_count"] > 0:
            specs.append(
                _notification_payload(
                    type=Notification.Type.READY_TO_PUBLISH,
                    title=f"{summary['ready_to_publish_count']} rescue draft(s) ready to publish",
                    body=(
                        f"{session.name} has publish-ready items waiting in the queue."
                    ),
                    link_path=f"/publish/{session.id}",
                    priority=Notification.Priority.NORMAL,
                    dedupe_key=f"ready:{user.id}:{session.id}:{summary['ready_to_publish_count']}",
                )
            )

        if (
            summary["missing_info_count"] > 0
            and session.move_out_deadline
            and session.move_out_deadline <= now + timedelta(hours=48)
        ):
            specs.append(
                _notification_payload(
                    type=Notification.Type.BLOCKED_SCAN,
                    title=f"{summary['missing_info_count']} draft(s) still missing info",
                    body=(
                        f"{session.name} is close to move-out. Finish the missing details before the deadline closes."
                    ),
                    link_path=f"/publish/{session.id}",
                    priority=Notification.Priority.HIGH,
                    dedupe_key=f"blocked:{user.id}:{session.id}:{summary['missing_info_count']}",
                )
            )

    tasks = (
        MoveOutTask.objects.select_related("scan_session")
        .filter(scan_session__owner=user, scan_session__is_demo=False)
        .exclude(status=MoveOutTask.Status.DONE)
    )
    for task in tasks:
        if not task.due_at or task.due_at > now + timedelta(hours=24):
            continue
        is_overdue = task.due_at < now
        specs.append(
            _notification_payload(
                type=Notification.Type.MOVE_OUT_TASK,
                title=task.title,
                body=(
                    f"{task.scan_session.name} has a task that is already overdue."
                    if is_overdue
                    else f"{task.scan_session.name} has a task due within the next 24 hours."
                ),
                link_path=f"/plan/{task.scan_session_id}",
                priority=Notification.Priority.HIGH if is_overdue else Notification.Priority.NORMAL,
                dedupe_key=f"task:{user.id}:{task.id}:{'overdue' if is_overdue else 'due-today'}",
            )
        )

    reservations = (
        Reservation.objects.select_related("listing", "listing__owner", "claimant")
        .filter(Q(claimant=user) | Q(listing__owner=user), listing__is_demo=False)
        .order_by("-updated_at")
    )
    for reservation in reservations:
        relationship = "owner" if reservation.listing.owner_id == user.id else "claimant"
        counterpart = (
            reservation.claimant.display_name
            if relationship == "owner"
            else reservation.listing.owner.display_name
        )
        action = reservation.status.replace("_", " ")
        specs.append(
            _notification_payload(
                type=Notification.Type.RESERVATION,
                title=f"{reservation.listing.title} was {action}",
                body=f"{counterpart} is connected to this handoff. Review the next step and pickup window.",
                link_path=f"/handoffs/{reservation.id}",
                priority=Notification.Priority.NORMAL,
                dedupe_key=f"reservation:{user.id}:{reservation.id}:{relationship}:{reservation.status}",
            )
        )

        if (
            reservation.status in {Reservation.Status.REQUESTED, Reservation.Status.CONFIRMED}
            and reservation.listing.available_until <= now + timedelta(hours=12)
        ):
            specs.append(
                _notification_payload(
                    type=Notification.Type.HANDOFF_URGENT,
                    title=f"Urgent handoff for {reservation.listing.title}",
                    body="This pickup window is close to the move-out deadline. Keep the handoff moving.",
                    link_path=f"/handoffs/{reservation.id}",
                    priority=Notification.Priority.HIGH,
                    dedupe_key=f"handoff:{user.id}:{reservation.id}:{relationship}:{reservation.status}",
                )
            )

    expired_with_token = (
        Listing.objects.filter(
            owner=user,
            status=Listing.Status.EXPIRED,
            is_demo=False,
            repost_token__isnull=False,
            repost_token_expires_at__gt=now,
        )
        .only("id", "title", "repost_token")
    )
    for listing in expired_with_token:
        specs.append(
            _notification_payload(
                type=Notification.Type.SYSTEM,
                title=f'"{listing.title}" expired — repost it?',
                body="Your listing expired. One click extends it for another 7 days.",
                link_path=f"/my-listings?repost={listing.id}&token={listing.repost_token}",
                priority=Notification.Priority.LOW,
                dedupe_key=f"repost:{user.id}:{listing.id}:{listing.repost_token}",
            )
        )

    return specs


def sync_user_notifications(user: UserType) -> dict[str, int]:
    desired_specs = build_notification_specs_for_user(user)
    desired_keys = {spec["dedupe_key"] for spec in desired_specs}
    existing = {
        notification.dedupe_key: notification
        for notification in Notification.objects.filter(user=user)
    }
    to_create: list[Notification] = []
    to_update: list[Notification] = []

    for spec in desired_specs:
        notification = existing.get(spec["dedupe_key"])
        if notification is None:
            to_create.append(Notification(user=user, **spec))
            continue

        changed = False
        for field in ("type", "title", "body", "link_path", "priority"):
            value = spec[field]
            if getattr(notification, field) != value:
                setattr(notification, field, value)
                changed = True

        if changed and notification.is_read:
            notification.is_read = False
            notification.read_at = None
        if changed:
            to_update.append(notification)

    if to_create:
        Notification.objects.bulk_create(to_create)
    if to_update:
        Notification.objects.bulk_update(
            to_update,
            ["type", "title", "body", "link_path", "priority", "is_read", "read_at"],
        )

    Notification.objects.filter(user=user).exclude(dedupe_key__in=desired_keys).delete()
    return {
        "created": len(to_create),
        "updated": len(to_update),
        "active": len(desired_specs),
    }


def notify_matching_rescue_requests(listing: Listing) -> int:
    """
    Called after a new listing is created. Uses Claude to score semantic similarity
    between the listing and all open rescue requests in the same category.
    Creates an AI_MATCH notification for seekers whose request scores >= 7/10.
    Returns the number of notifications created.
    """
    import json as _json
    import os
    import re

    now = timezone.now()
    candidates = list(
        RescueRequest.objects.select_related("seeker")
        .filter(
            category=listing.category,
            status=RescueRequest.Status.OPEN,
            needed_by__gt=now,
        )
        .exclude(seeker=listing.owner)
        [:20]
    )
    if not candidates:
        return 0

    if not ai_available():
        return 0

    listing_text = f"Title: {listing.title}\nDescription: {(listing.description or '').strip()[:300]}"
    requests_text = "\n".join(
        f'{i + 1}. [id:{req.id}] Title: {req.title}; Notes: {(req.description or "").strip()[:120]}'
        for i, req in enumerate(candidates)
    )

    prompt = (
        "You are a matching engine for a college campus item marketplace.\n"
        "Score how well a newly posted listing matches each rescue request on a scale of 0–10.\n"
        "10 = near-perfect match (same item, right condition/price), 7+ = worth notifying the seeker, "
        "below 7 = skip.\n\n"
        f"New listing:\n{listing_text}\n\n"
        f"Open rescue requests:\n{requests_text}\n\n"
        'Respond ONLY with a JSON array: [{"id": <int>, "score": <0-10>}, ...]. No explanation.'
    )

    try:
        scores_list = generate_json(prompt, max_tokens=300)
    except Exception:
        return 0

    if not isinstance(scores_list, list):
        return 0

    scores = {item["id"]: item["score"] for item in scores_list if isinstance(item, dict) and "id" in item and "score" in item}

    created_count = 0
    category_label = listing.category.replace("_", " ").capitalize()
    for req in candidates:
        score = scores.get(req.id, 0)
        if score < 7:
            continue
        _, created = Notification.objects.get_or_create(
            user=req.seeker,
            dedupe_key=f"ai_match:{req.id}:{listing.id}",
            defaults={
                "type": Notification.Type.AI_MATCH,
                "title": f'A {category_label} listing matches your request',
                "body": (
                    f'"{listing.title}" was just posted and looks like a strong match for '
                    f'your "{req.title}" request. Check it out before move-out.'
                ),
                "link_path": f"/listings/{listing.id}",
                "priority": Notification.Priority.NORMAL,
            },
        )
        if created:
            created_count += 1

    return created_count


def sync_notifications_for_all_users() -> dict[str, int]:
    total_created = 0
    total_updated = 0
    total_active = 0
    for user in User.objects.filter(is_active=True):
        counts = sync_user_notifications(user)
        total_created += counts["created"]
        total_updated += counts["updated"]
        total_active += counts["active"]
    return {
        "created": total_created,
        "updated": total_updated,
        "active": total_active,
    }
