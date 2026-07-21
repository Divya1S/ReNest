from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.http import StreamingHttpResponse
from django.db.models import Count, DecimalField, F, FloatField, Q, Sum, Value
from django.db.models.functions import Greatest, TruncDate, TruncWeek
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

if TYPE_CHECKING:
    from accounts.models import User as UserType

from ..models import (
    Listing,
    ListingReport,
    ListingUpdate,
    MoveOutTask,
    Notification,
    Reservation,
    RescueRequest,
    RoomScanItemDraft,
    RoomScanSession,
    SavedListing,
)
from ..publish_flow import get_publish_readiness
from ..serializers import (
    ListingSerializer,
    MoveOutTaskSerializer,
    NotificationSerializer,
    RescueRequestSerializer,
    RoomScanItemDraftSerializer,
    compute_scan_summary,
)
from ..trust import build_user_trust_summary, get_pending_feedback_queryset
from .helpers import get_rescue_request_queryset
from .requests import build_match_center_payload
from .scan import get_scan_session_queryset, get_task_queryset, serialize_summary
from dormcycle.typed import current_user


def _build_percentage(numerator: int, denominator: int) -> int:
    if not denominator:
        return 0
    return round((numerator / denominator) * 100)


def _format_money_value(amount: Any) -> str:
    return f"{Decimal(amount or 0):.2f}"


def build_insights_payload(user: UserType, request: Request) -> dict[str, Any]:
    now = timezone.now()
    campus_name = user.campus_name or "Your campus"
    rescued_statuses = [Listing.Status.PICKED_UP, Listing.Status.DONATED]
    active_statuses = [Listing.Status.AVAILABLE, Listing.Status.RESERVED]
    open_report_statuses = [ListingReport.Status.OPEN, ListingReport.Status.REVIEWING]

    # ── Scoping filters (campus vs. user-only fallback) ──────────────────────
    if user.campus_name:
        campus_listing_filter = Q(owner__campus_name=user.campus_name)
        campus_req_filter = Q(seeker__campus_name=user.campus_name)
        campus_res_filter = Q(listing__owner__campus_name=user.campus_name)
        campus_report_filter = Q(listing__owner__campus_name=user.campus_name)
    else:
        campus_listing_filter = Q(owner=user)
        campus_req_filter = Q(seeker=user)
        campus_res_filter = Q(claimant=user) | Q(listing__owner=user)
        campus_report_filter = Q(listing__owner=user)

    # ── Base querysets (no evaluation yet) ───────────────────────────────────
    my_listings_qs = Listing.objects.filter(owner=user, is_demo=False)
    campus_listings_qs = Listing.objects.filter(campus_listing_filter, is_demo=False)
    campus_req_qs = RescueRequest.objects.filter(campus_req_filter)
    campus_res_qs = Reservation.objects.filter(campus_res_filter, listing__is_demo=False)
    campus_report_qs = ListingReport.objects.filter(campus_report_filter, listing__is_demo=False)

    # ── my_impact (3 queries) ─────────────────────────────────────────────────
    impact_agg = my_listings_qs.aggregate(
        active_listings=Count(
            "id",
            filter=Q(status__in=active_statuses, available_until__gte=now),
        ),
        published_from_scan=Count("id", filter=Q(source_scan_session__isnull=False)),
        rescued_items=Count("id", filter=Q(status__in=rescued_statuses)),
        estimated_value=Sum(
            Greatest(
                F("estimated_retail_value") - F("price_amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
            filter=Q(status__in=rescued_statuses),
        ),
    )
    res_agg = (
        Reservation.objects.filter(
            Q(claimant=user) | Q(listing__owner=user),
            listing__is_demo=False,
        )
        .exclude(status=Reservation.Status.CANCELLED)
        .aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status=Reservation.Status.COMPLETED)),
        )
    )
    trust_summary = build_user_trust_summary(user)
    pending_feedback_count = get_pending_feedback_queryset(user).count()

    # ── campus_snapshot (2 queries) ───────────────────────────────────────────
    campus_active_count = campus_listings_qs.filter(
        status__in=active_statuses, available_until__gte=now
    ).count()
    req_counts = campus_req_qs.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status=RescueRequest.Status.OPEN)),
        matched=Count("id", filter=Q(status=RescueRequest.Status.MATCHED)),
        fulfilled=Count("id", filter=Q(status=RescueRequest.Status.FULFILLED)),
        urgent=Count(
            "id",
            filter=Q(status=RescueRequest.Status.OPEN) & (
                Q(needed_by__lte=now + timedelta(days=2))
                | Q(urgency=RescueRequest.Urgency.URGENT)
            ),
        ),
    )

    # ── reports (2 queries) ───────────────────────────────────────────────────
    campus_open_reports_count = campus_report_qs.filter(
        status__in=open_report_statuses
    ).count()
    reports_on_my_count = ListingReport.objects.filter(
        listing__owner=user, status__in=open_report_statuses
    ).count()
    reports_filed_count = ListingReport.objects.filter(reporter=user).count()

    # ── category_market_map (3 queries → dict lookups) ────────────────────────
    supply_by_cat = dict(
        campus_listings_qs.filter(status__in=active_statuses, available_until__gte=now)
        .values("category")
        .annotate(n=Count("id"))
        .values_list("category", "n")
    )
    req_rows = campus_req_qs.values("category", "status").annotate(n=Count("id"))
    req_by_cat_status: dict[str, dict[str, int]] = {}
    for row in req_rows:
        req_by_cat_status.setdefault(row["category"], {})[row["status"]] = row["n"]
    rescued_by_cat = dict(
        campus_listings_qs.filter(status__in=rescued_statuses)
        .values("category")
        .annotate(n=Count("id"))
        .values_list("category", "n")
    )

    category_market_map: list[dict[str, Any]] = []
    highest_pressure_entry: dict[str, Any] | None = None
    for category_key, category_label in Listing.Category.choices:
        cat_req = req_by_cat_status.get(category_key, {})
        supply = supply_by_cat.get(category_key, 0)
        demand = cat_req.get(RescueRequest.Status.OPEN, 0) + cat_req.get(RescueRequest.Status.MATCHED, 0)
        matched = cat_req.get(RescueRequest.Status.MATCHED, 0)
        fulfilled = cat_req.get(RescueRequest.Status.FULFILLED, 0)
        rescued = rescued_by_cat.get(category_key, 0)
        pressure = max(demand - supply, 0)
        recommendation = (
            "High demand pressure" if pressure >= 2
            else "Needs more supply" if pressure == 1
            else "Balanced"
        )
        entry = {
            "key": category_key,
            "label": category_label,
            "supply": supply,
            "demand": demand,
            "matched": matched,
            "fulfilled": fulfilled,
            "rescued": rescued,
            "pressure": pressure,
            "recommendation": recommendation,
        }
        category_market_map.append(entry)
        if highest_pressure_entry is None or entry["pressure"] > highest_pressure_entry["pressure"]:
            highest_pressure_entry = entry

    # ── funnels (2 queries) ───────────────────────────────────────────────────
    # Re-derive totals-by-status from the already-fetched req_rows aggregate
    req_totals_by_status: dict[str, int] = {}
    for row in req_rows:
        req_totals_by_status[row["status"]] = req_totals_by_status.get(row["status"], 0) + row["n"]

    request_funnel = [
        {"name": "Open", "value": req_totals_by_status.get(RescueRequest.Status.OPEN, 0)},
        {"name": "Matched", "value": req_totals_by_status.get(RescueRequest.Status.MATCHED, 0)},
        {"name": "Fulfilled", "value": req_totals_by_status.get(RescueRequest.Status.FULFILLED, 0)},
        {"name": "Closed", "value": req_totals_by_status.get(RescueRequest.Status.CLOSED, 0)},
    ]
    handoff_status_counts = dict(
        campus_res_qs.values("status").annotate(n=Count("id")).values_list("status", "n")
    )
    handoff_funnel = [
        {"name": label, "value": handoff_status_counts.get(status_key, 0)}
        for status_key, label in Reservation.Status.choices
    ]

    # ── weekly_activity (4 TruncWeek queries) ────────────────────────────────
    current_week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    six_weeks_ago = current_week_start - timedelta(weeks=5)
    week_window = Q(updated_at__gte=six_weeks_ago, updated_at__lt=current_week_start + timedelta(days=7))

    rescued_by_week = dict(
        campus_listings_qs.filter(status__in=rescued_statuses)
        .filter(week_window)
        .annotate(week=TruncWeek("updated_at"))
        .values("week")
        .annotate(n=Count("id"))
        .values_list("week", "n")
    )
    requests_by_week = dict(
        campus_req_qs
        .filter(created_at__gte=six_weeks_ago, created_at__lt=current_week_start + timedelta(days=7))
        .annotate(week=TruncWeek("created_at"))
        .values("week")
        .annotate(n=Count("id"))
        .values_list("week", "n")
    )
    matched_by_week = dict(
        campus_req_qs
        .filter(status__in=[RescueRequest.Status.MATCHED, RescueRequest.Status.FULFILLED])
        .filter(week_window)
        .annotate(week=TruncWeek("updated_at"))
        .values("week")
        .annotate(n=Count("id"))
        .values_list("week", "n")
    )
    handoffs_by_week = dict(
        campus_res_qs.filter(status=Reservation.Status.COMPLETED)
        .filter(week_window)
        .annotate(week=TruncWeek("updated_at"))
        .values("week")
        .annotate(n=Count("id"))
        .values_list("week", "n")
    )

    weekly_activity = []
    for weeks_back in range(5, -1, -1):
        week_start = current_week_start - timedelta(weeks=weeks_back)
        weekly_activity.append({
            "label": week_start.strftime("%b %d"),
            "rescued": rescued_by_week.get(week_start, 0),
            "requests": requests_by_week.get(week_start, 0),
            "matched": matched_by_week.get(week_start, 0),
            "handoffs": handoffs_by_week.get(week_start, 0),
        })

    # ── match_center (already optimised) ─────────────────────────────────────
    match_center = build_match_center_payload(user, request)

    # ── recommendations ───────────────────────────────────────────────────────
    recommendations = []
    if match_center["stats"]["fulfillable_needs"]:
        recommendations.append({
            "title": "Convert active listings into request matches",
            "detail": (
                f"{match_center['stats']['fulfillable_needs']} of your live listings already line up "
                "with open student requests."
            ),
            "action_label": "Open Requests",
            "action_path": "/requests",
            "tone": "teal",
        })
    if match_center["stats"]["requests_with_matches"]:
        recommendations.append({
            "title": "Claim a live match for your own requests",
            "detail": (
                f"{match_center['stats']['requests_with_matches']} of your requests already have "
                "eligible listings on campus."
            ),
            "action_label": "Review Matches",
            "action_path": "/requests",
            "tone": "blue",
        })
    if reports_on_my_count:
        recommendations.append({
            "title": "Review trust issues on your listings",
            "detail": (
                f"{reports_on_my_count} listing report"
                f"{'' if reports_on_my_count == 1 else 's'} need attention."
            ),
            "action_label": "Open Trust Center",
            "action_path": "/trust",
            "tone": "amber",
        })
    if pending_feedback_count:
        recommendations.append({
            "title": "Close the loop on completed handoffs",
            "detail": (
                f"You still have {pending_feedback_count} pickup review"
                f"{'' if pending_feedback_count == 1 else 's'} waiting."
            ),
            "action_label": "Open Handoffs",
            "action_path": "/my-reservations",
            "tone": "box",
        })
    if highest_pressure_entry and highest_pressure_entry["pressure"] > 0:
        recommendations.append({
            "title": f"{highest_pressure_entry['label']} is the biggest campus gap",
            "detail": (
                f"Demand is ahead of supply by {highest_pressure_entry['pressure']} in "
                f"{highest_pressure_entry['label'].lower()}."
            ),
            "action_label": "Open Browse",
            "action_path": "/browse",
            "tone": "blue",
        })
    if not recommendations:
        recommendations.append({
            "title": "Your rescue network is balanced right now",
            "detail": "Keep scanning and publishing items to maintain healthy campus coverage.",
            "action_label": "Start a Scan",
            "action_path": "/scan",
            "tone": "teal",
        })

    return {
        "my_impact": {
            "active_listings": impact_agg["active_listings"] or 0,
            "published_from_scan": impact_agg["published_from_scan"] or 0,
            "rescued_items": impact_agg["rescued_items"] or 0,
            "estimated_value_recirculated": _format_money_value(impact_agg["estimated_value"]),
            "average_rating": trust_summary["average_rating"],
            "feedback_received": trust_summary["total_feedback_received"],
            "completion_rate": _build_percentage(res_agg["completed"] or 0, res_agg["total"] or 0),
        },
        "campus_snapshot": {
            "campus_name": campus_name,
            "active_listings": campus_active_count,
            "open_requests": req_counts["open"],
            "matched_requests": req_counts["matched"],
            "fulfilled_requests": req_counts["fulfilled"],
            "urgent_requests": req_counts["urgent"],
            "open_reports": campus_open_reports_count,
            "request_match_rate": _build_percentage(
                req_counts["matched"] + req_counts["fulfilled"],
                req_counts["total"],
            ),
            "request_fulfillment_rate": _build_percentage(
                req_counts["fulfilled"],
                req_counts["total"],
            ),
        },
        "request_funnel": request_funnel,
        "handoff_funnel": handoff_funnel,
        "weekly_activity": weekly_activity,
        "category_market_map": category_market_map,
        "ops_snapshot": {
            "requests_you_can_fulfill": match_center["stats"]["fulfillable_needs"],
            "your_requests_with_matches": match_center["stats"]["requests_with_matches"],
            "pending_feedback": pending_feedback_count,
            "open_reports_on_your_listings": reports_on_my_count,
            "reports_you_filed": reports_filed_count,
            "campus_open_reports": campus_open_reports_count,
        },
        "recommendations": recommendations[:4],
    }


_INSIGHTS_TTL = 300  # 5 min
_DASHBOARD_TTL = 60  # 1 min (reservation states change often)


def _insights_key(user_id: int | None) -> str:
    return f"insights:{user_id}"


def _dashboard_key(user_id: int | None) -> str:
    return f"dashboard:{user_id}"


def invalidate_user_dashboard_cache(user_id: int | None) -> None:
    cache.delete_many([_insights_key(user_id), _dashboard_key(user_id)])


@extend_schema(exclude=True)
class InsightsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        key = _insights_key(request.user.pk)
        payload = cache.get(key)
        if payload is None:
            payload = build_insights_payload(current_user(request), request)
            cache.set(key, payload, _INSIGHTS_TTL)
        return Response(payload)


@extend_schema(exclude=True)
class DashboardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        key = _dashboard_key(request.user.pk)
        cached = cache.get(key)
        if cached is not None:
            return Response(cached)
        user = current_user(request)
        now = timezone.now()

        owned_listings = Listing.objects.filter(owner=user, is_demo=False)
        saved_links = SavedListing.objects.select_related("listing", "listing__owner").filter(
            user=user,
            listing__is_demo=False,
        )
        outgoing_reservations = Reservation.objects.select_related("listing", "listing__owner").filter(
            claimant=user,
            listing__is_demo=False,
        )
        incoming_reservations = Reservation.objects.select_related("listing", "claimant").filter(
            listing__owner=user,
            listing__is_demo=False,
        )
        scan_sessions = get_scan_session_queryset(user)
        move_out_tasks = get_task_queryset(user)
        notifications = Notification.objects.filter(user=user)
        unread_notifications = list(notifications.filter(is_read=False)[:4])
        request_queryset = get_rescue_request_queryset(user)
        my_requests = request_queryset.filter(seeker=user)
        campus_open_requests = list(
            request_queryset.exclude(seeker=user)
            .filter(status=RescueRequest.Status.OPEN)
            .order_by("needed_by", "-created_at")[:4]
        )
        my_matched_requests = list(
            my_requests.filter(status=RescueRequest.Status.MATCHED).order_by("needed_by", "-updated_at")[:4]
        )

        category_breakdown = [
            {"name": item["category"].replace("_", " ").title(), "value": item["count"]}
            for item in owned_listings.values("category").annotate(count=Count("id")).order_by("-count")
        ]
        status_breakdown = [
            {"name": item["status"].replace("_", " ").title(), "value": item["count"]}
            for item in owned_listings.values("status").annotate(count=Count("id")).order_by("-count")
        ]

        urgent_listings = list(
            owned_listings.filter(
                status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
                available_until__gte=now,
                available_until__lte=now + timedelta(days=2),
            )[:4]
        )
        incoming_action_reservations = list(
            incoming_reservations.filter(
                status__in=[Reservation.Status.REQUESTED, Reservation.Status.CONFIRMED]
            ).order_by("-updated_at")[:4]
        )
        pending_feedback_reservations = list(get_pending_feedback_queryset(user)[:4])
        match_center = build_match_center_payload(user, request)
        saved_preview = [link.listing for link in saved_links[:4]]

        updates = ListingUpdate.objects.select_related("listing", "author").filter(
            Q(author=user)
            | Q(listing__owner=user)
            | Q(listing__reservations__claimant=user),
            listing__is_demo=False,
        ).distinct()[:6]

        activity = []
        for reservation in list(incoming_reservations[:3]) + list(outgoing_reservations[:3]):
            activity.append(
                {
                    "type": "reservation",
                    "title": reservation.listing.title,
                    "timestamp": reservation.updated_at,
                    "description": (
                        f"{reservation.claimant.display_name} {reservation.status.replace('_', ' ')} "
                        f"the handoff for {reservation.listing.title}."
                    ),
                }
            )

        for update in updates:
            activity.append(
                {
                    "type": "update",
                    "title": update.listing.title,
                    "timestamp": update.created_at,
                    "description": f"{update.author.display_name} posted: {update.body}",
                }
            )

        for session in scan_sessions[:3]:
            summary = compute_scan_summary(session)
            activity.append(
                {
                    "type": "scan",
                    "title": session.name,
                    "timestamp": session.updated_at,
                    "description": (
                        f"{summary['handled_items']} of {summary['total_items']} rescue drafts are cleared "
                        f"for {session.room_label}."
                    ),
                }
            )

        activity.sort(key=lambda item: str(item["timestamp"]), reverse=True)

        scan_overview = []
        total_scan_savings = Decimal("0.00")
        pending_scan_items = 0
        donation_routes = 0
        ready_publish_items = []
        blocked_scan_items = []
        for session in scan_sessions[:4]:
            summary = compute_scan_summary(session)
            total_scan_savings += Decimal(summary["estimated_student_savings"])
            pending_scan_items += summary["pending_items"]
            donation_routes += summary["donation_count"]
            scan_overview.append(
                {
                    "id": session.id,
                    "name": session.name,
                    "status": session.status,
                    "room_label": session.room_label,
                    "room_type": session.room_type,
                    "move_out_deadline": session.move_out_deadline,
                    "progress_percent": session.progress_percent,
                    "summary": serialize_summary(summary),
                }
            )
            if summary["missing_info_count"] > 0:
                blocked_scan_items.append(
                    {
                        "id": session.id,
                        "name": session.name,
                        "room_label": session.room_label,
                        "progress_percent": session.progress_percent,
                        "move_out_deadline": session.move_out_deadline,
                        "missing_info_count": summary["missing_info_count"],
                        "ready_to_publish_count": summary["ready_to_publish_count"],
                    }
                )

        ready_candidates = (
            RoomScanItemDraft.objects.select_related(
                "scan_session",
                "donation_hub",
                "linked_listing",
                "source_image",
            )
            .filter(
                scan_session__owner=user,
                scan_session__is_demo=False,
                triage_status=RoomScanItemDraft.TriageStatus.SELL,
                linked_listing__isnull=True,
            )
            .order_by("-updated_at")
        )
        ready_publish_items = [
            item for item in ready_candidates if get_publish_readiness(item) == "ready"
        ][:6]

        critical_tasks = list(
            move_out_tasks.exclude(status=MoveOutTask.Status.DONE)
            .filter(Q(due_at__lt=now) | Q(due_at__lte=now + timedelta(hours=24)))
            .order_by("due_at", "-updated_at")[:6]
        )
        overdue_task_count = move_out_tasks.exclude(status=MoveOutTask.Status.DONE).filter(
            due_at__lt=now
        ).count()
        due_today_task_count = move_out_tasks.exclude(status=MoveOutTask.Status.DONE).filter(
            due_at__gte=now,
            due_at__lte=now + timedelta(hours=24),
        ).count()

        response = Response(
            {
                "stats": {
                    "active_listings": owned_listings.filter(
                        status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
                        available_until__gte=now,
                    ).count(),
                    "saved_items": saved_links.count(),
                    "incoming_requests": incoming_reservations.filter(
                        status=Reservation.Status.REQUESTED
                    ).count(),
                    "outgoing_claims": outgoing_reservations.exclude(
                        status=Reservation.Status.CANCELLED
                    ).count(),
                    "rescued_total": owned_listings.filter(
                        status__in=[Listing.Status.PICKED_UP, Listing.Status.DONATED]
                    ).count(),
                    "active_scans": scan_sessions.exclude(status=RoomScanSession.Status.COMPLETED).count(),
                    "pending_scan_items": pending_scan_items,
                    "donation_routes": donation_routes,
                    "ready_to_publish_now": len(ready_publish_items),
                    "blocked_scans": len(blocked_scan_items),
                    "listings_expiring_soon": len(urgent_listings),
                    "incoming_pickup_actions": len(incoming_action_reservations),
                    "tasks_due_today": due_today_task_count,
                    "overdue_tasks": overdue_task_count,
                    "feedback_to_leave": len(pending_feedback_reservations),
                    "open_requests": my_requests.filter(status=RescueRequest.Status.OPEN).count(),
                    "matched_requests": my_requests.filter(status=RescueRequest.Status.MATCHED).count(),
                    "fulfillable_needs": match_center["stats"]["fulfillable_needs"],
                    "requests_with_matches": match_center["stats"]["requests_with_matches"],
                    "scan_savings_total": f"{total_scan_savings:.2f}",
                    "unread_notifications": notifications.filter(is_read=False).count(),
                },
                "category_breakdown": category_breakdown,
                "status_breakdown": status_breakdown,
                "urgent_listings": ListingSerializer(
                    urgent_listings,
                    many=True,
                    context={"request": request},
                ).data,
                "saved_preview": ListingSerializer(
                    saved_preview,
                    many=True,
                    context={"request": request},
                ).data,
                "scan_overview": scan_overview,
                "action_queues": {
                    "ready_to_publish_now": RoomScanItemDraftSerializer(
                        ready_publish_items,
                        many=True,
                        context={"request": request},
                    ).data,
                    "scans_blocked_by_missing_info": blocked_scan_items,
                    "listings_expiring_soon": ListingSerializer(
                        urgent_listings,
                        many=True,
                        context={"request": request},
                    ).data,
                    "incoming_pickup_actions": [
                        {
                            "reservation_id": reservation.id,
                            "listing_id": reservation.listing_id,
                            "listing_title": reservation.listing.title,
                            "pickup_time_window": reservation.pickup_time_window,
                            "status": reservation.status,
                            "claimant_name": reservation.claimant.display_name,
                            "updated_at": reservation.updated_at,
                        }
                        for reservation in incoming_action_reservations
                    ],
                    "handoffs_waiting_for_feedback": [
                        {
                            "reservation_id": reservation.id,
                            "listing_id": reservation.listing_id,
                            "listing_title": reservation.listing.title,
                            "counterparty_name": (
                                reservation.claimant.display_name
                                if reservation.listing.owner_id == user.id
                                else reservation.listing.owner.display_name
                            ),
                            "updated_at": reservation.updated_at,
                        }
                        for reservation in pending_feedback_reservations
                    ],
                    "my_request_matches": RescueRequestSerializer(
                        my_matched_requests,
                        many=True,
                        context={"request": request},
                    ).data,
                    "campus_open_requests": RescueRequestSerializer(
                        campus_open_requests,
                        many=True,
                        context={"request": request},
                    ).data,
                    "fulfill_opportunities": match_center["fulfill_opportunities"][:3],
                    "request_recommendations": match_center["request_recommendations"][:3],
                    "move_out_tasks": MoveOutTaskSerializer(
                        critical_tasks,
                        many=True,
                        context={"request": request},
                    ).data,
                },
                "notification_preview": NotificationSerializer(
                    unread_notifications,
                    many=True,
                ).data,
                "recent_activity": [
                    {
                        "type": item["type"],
                        "title": item["title"],
                        "timestamp": item["timestamp"],
                        "description": item["description"],
                    }
                    for item in activity[:8]
                ],
            }
        )
        cache.set(key, response.data, _DASHBOARD_TTL)
        return response


class IsCampusManager(permissions.BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or getattr(request.user, "is_campus_manager", False))
        )


@extend_schema(exclude=True)
class CampusAnalyticsView(APIView):
    """
    Staff / campus-manager analytics dashboard.
    GET /api/campus-analytics?campus=<name>&weeks=12
    """

    permission_classes = [IsCampusManager]

    def get(self, request: Request) -> Response:
        campus = request.query_params.get("campus", "") or getattr(request.user, "campus_name", "")
        weeks = min(int(request.query_params.get("weeks", 12)), 52)
        now = timezone.now()
        since = now - timedelta(weeks=weeks)

        base_qs = Listing.objects.filter(is_demo=False)
        if campus:
            base_qs = base_qs.filter(owner__campus_name__iexact=campus)

        # ── Weekly listing volume ──────────────────────────────────────────
        weekly_volume = (
            base_qs.filter(created_at__gte=since)
            .annotate(week=TruncWeek("created_at"))
            .values("week")
            .annotate(posted=Count("id"))
            .order_by("week")
        )

        rescued_statuses = [Listing.Status.PICKED_UP, Listing.Status.DONATED]
        weekly_rescued = (
            base_qs.filter(updated_at__gte=since, status__in=rescued_statuses)
            .annotate(week=TruncWeek("updated_at"))
            .values("week")
            .annotate(rescued=Count("id"))
            .order_by("week")
        )

        # Merge into a single list keyed by ISO week string
        volume_map: dict[str, dict] = {}
        for row in weekly_volume:
            key = row["week"].strftime("%Y-%m-%d")
            volume_map.setdefault(key, {"week": key, "posted": 0, "rescued": 0})
            volume_map[key]["posted"] = row["posted"]
        for row in weekly_rescued:
            key = row["week"].strftime("%Y-%m-%d")
            volume_map.setdefault(key, {"week": key, "posted": 0, "rescued": 0})
            volume_map[key]["rescued"] = row["rescued"]
        weekly_chart = sorted(volume_map.values(), key=lambda r: r["week"])

        # ── Category breakdown ────────────────────────────────────────────
        category_breakdown = list(
            base_qs.filter(created_at__gte=since)
            .values("category")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # ── Top pickup zones ──────────────────────────────────────────────
        top_zones = list(
            base_qs.filter(created_at__gte=since)
            .exclude(pickup_zone="")
            .values("pickup_zone")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )

        # ── Hub utilization ───────────────────────────────────────────────
        from hubs.models import DonationHub
        hub_qs = DonationHub.objects.filter(active=True)
        if campus:
            hub_qs = hub_qs.filter(campus_name__iexact=campus)

        hub_utilization = []
        for hub in hub_qs.annotate(donated_count=Count("donated_listings")):
            utilization_pct = None
            if hub.capacity:
                utilization_pct = round(hub.donated_count / hub.capacity * 100, 1)
            hub_utilization.append({
                "hub_id": hub.pk,
                "name": hub.name,
                "zone": hub.zone_label,
                "capacity": hub.capacity,
                "donated_items": hub.donated_count,
                "utilization_pct": utilization_pct,
            })

        # ── Cohort retention (% of users who post more than once) ─────────
        users_qs = Listing.objects.filter(is_demo=False, created_at__gte=since)
        if campus:
            users_qs = users_qs.filter(owner__campus_name__iexact=campus)

        poster_counts = (
            users_qs.values("owner_id")
            .annotate(listing_count=Count("id"))
        )
        total_posters = poster_counts.count()
        repeat_posters = poster_counts.filter(listing_count__gt=1).count()
        retention_pct = round(repeat_posters / total_posters * 100, 1) if total_posters else 0.0

        # ── Summary stats ─────────────────────────────────────────────────
        total_posted = base_qs.filter(created_at__gte=since).count()
        total_rescued = base_qs.filter(updated_at__gte=since, status__in=rescued_statuses).count()
        total_retail = base_qs.filter(
            updated_at__gte=since, status__in=rescued_statuses
        ).aggregate(total=Sum("estimated_retail_value"))["total"] or 0

        rescue_rate = round(total_rescued / total_posted * 100, 1) if total_posted else 0.0

        return Response({
            "campus": campus or "all",
            "period_weeks": weeks,
            "summary": {
                "total_posted": total_posted,
                "total_rescued": total_rescued,
                "rescue_rate_pct": rescue_rate,
                "total_retail_rescued": float(total_retail),
                "total_posters": total_posters,
                "repeat_posters": repeat_posters,
                "retention_pct": retention_pct,
            },
            "weekly_chart": weekly_chart,
            "category_breakdown": category_breakdown,
            "top_pickup_zones": top_zones,
            "hub_utilization": hub_utilization,
        })


class CampusAnalyticsExportView(APIView):
    """GET /api/campus-analytics/export.csv — streams all listings + reservations as CSV."""

    permission_classes = [IsCampusManager]

    def get(self, request: Request) -> "Response | StreamingHttpResponse":
        import csv

        campus = getattr(request.user, "campus", None)
        qs = (
            Listing.all_objects.filter(owner__campus=campus)
            .select_related("owner")
            .prefetch_related("reservations")
            .order_by("-created_at")
        )

        rescued_statuses = {Listing.Status.PICKED_UP, Listing.Status.DONATED}

        def row_iter() -> Any:
            header = [
                "id", "title", "category", "condition", "status",
                "price_type", "price_amount", "estimated_retail_value",
                "pickup_zone", "building", "owner_email",
                "created_at", "claimed_at", "value_rescued",
            ]
            yield header
            for listing in qs.iterator(chunk_size=200):
                claimed_at = ""
                if listing.status in rescued_statuses:
                    completed = listing.reservations.filter(
                        status=Reservation.Status.COMPLETED
                    ).order_by("updated_at").first()
                    if completed:
                        claimed_at = completed.updated_at.isoformat()
                value_rescued = (
                    float(listing.estimated_retail_value)
                    if listing.status in rescued_statuses
                    else 0.0
                )
                yield [
                    listing.id,
                    listing.title,
                    listing.category,
                    listing.condition,
                    listing.status,
                    listing.price_type,
                    float(listing.price_amount),
                    float(listing.estimated_retail_value),
                    listing.pickup_zone,
                    listing.building,
                    listing.owner.email,
                    listing.created_at.isoformat(),
                    claimed_at,
                    value_rescued,
                ]

        class EchoCsv:
            def write(self, value: Any) -> Any:
                return value

        writer = csv.writer(EchoCsv())
        response = StreamingHttpResponse(
            (writer.writerow(row) for row in row_iter()),
            content_type="text/csv",
        )
        response["Content-Disposition"] = 'attachment; filename="dormcycle-analytics.csv"'
        return response


class ImpactBenchmarkView(APIView):
    """GET /api/impact/benchmark — this campus vs. peers (anonymised percentiles)."""

    def get(self, request: Request) -> Response:
        campus = getattr(request.user, "campus", None)
        if not campus:
            return Response({"detail": "No campus associated with your account."}, status=400)

        from accounts.models import Campus

        # Aggregate per campus
        campus_stats = []
        for c in Campus.objects.filter(active=True, onboarding_status="approved"):
            member_count = c.members.count() or 1
            claimed = Reservation.objects.filter(
                listing__owner__campus=c, status=Reservation.Status.COMPLETED
            ).count()
            campus_stats.append({"campus_id": c.id, "per_member": claimed / member_count})

        if not campus_stats:
            return Response({"detail": "Not enough data."}, status=200)

        sorted_rates = sorted(s["per_member"] for s in campus_stats)
        my_stat = next((s for s in campus_stats if s["campus_id"] == campus.id), None)
        my_rate = my_stat["per_member"] if my_stat else 0.0

        rank = sum(1 for r in sorted_rates if r <= my_rate)
        percentile = round(rank / len(sorted_rates) * 100)

        # Absolute counts for this campus
        my_claimed = Reservation.objects.filter(
            listing__owner__campus=campus, status=Reservation.Status.COMPLETED
        ).count()
        my_value = float(
            Reservation.objects.filter(
                listing__owner__campus=campus, status=Reservation.Status.COMPLETED
            ).aggregate(v=Sum("listing__estimated_retail_value"))["v"] or 0
        )

        return Response({
            "campus_name": campus.name,
            "claimed_count": my_claimed,
            "total_value_rescued": my_value,
            "percentile": percentile,
            "total_campuses": len(campus_stats),
            "label": _benchmark_label(percentile),
        })


def _benchmark_label(percentile: int) -> str:
    if percentile >= 90:
        return "Top 10% campus"
    if percentile >= 75:
        return "Top 25% campus"
    if percentile >= 50:
        return "Above average"
    if percentile >= 25:
        return "Below average"
    return "Early stage"
