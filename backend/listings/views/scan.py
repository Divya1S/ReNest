from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

if TYPE_CHECKING:
    from accounts.models import User as UserType

from hubs.models import DonationHub

from ..ai_client import ai_available
from ..permissions import IsEmailVerified
from ..throttles import AiDetectThrottle
from ..models import (
    Listing,
    MoveOutTask,
    RoomScanImage,
    RoomScanItemDraft,
    RoomScanSession,
    seed_default_move_out_tasks,
    sync_system_move_out_tasks,
)
from ..publish_flow import (
    build_missing_fields,
    build_publish_description,
    get_publish_preset,
    get_publish_readiness,
    get_serialized_publish_presets,
)
from ..serializers import (
    ListingSerializer,
    MoveOutTaskSerializer,
    RoomScanItemDraftSerializer,
    RoomScanSessionSerializer,
    compute_scan_summary,
)
from ..image_utils import compress_image
from .helpers import annotate_listing_queryset
from dormcycle.typed import current_user


TRIAGE_ORDER = [
    RoomScanItemDraft.TriageStatus.REVIEW,
    RoomScanItemDraft.TriageStatus.SELL,
    RoomScanItemDraft.TriageStatus.DONATE,
    RoomScanItemDraft.TriageStatus.KEEP,
    RoomScanItemDraft.TriageStatus.TOSS,
    RoomScanItemDraft.TriageStatus.DONE,
]

SCAN_SUGGESTIONS: dict[str, list[dict[str, Any]]] = {
    RoomScanSession.RoomType.DORM_ROOM: [
        {
            "title": "Desk lamp",
            "category": Listing.Category.LIGHTING,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.LOW_COST,
            "price_amount": Decimal("8.00"),
            "estimated_retail_value": Decimal("24.00"),
            "triage_status": RoomScanItemDraft.TriageStatus.SELL,
            "notes": "Suggested from a classic dorm desk-zone item.",
        },
        {
            "title": "Storage bin",
            "category": Listing.Category.STORAGE,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.FREE,
            "price_amount": Decimal("0.00"),
            "estimated_retail_value": Decimal("18.00"),
            "triage_status": RoomScanItemDraft.TriageStatus.SELL,
            "notes": "Suggested as an easy rescue item for first-week storage.",
        },
        {
            "title": "Mini fan",
            "category": Listing.Category.COMFORT,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.LOW_COST,
            "price_amount": Decimal("10.00"),
            "estimated_retail_value": Decimal("28.00"),
            "triage_status": RoomScanItemDraft.TriageStatus.SELL,
            "notes": "Suggested because cooling gear gets rebought every move-in season.",
        },
        {
            "title": "Desk supplies caddy",
            "category": Listing.Category.SUPPLIES,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.FREE,
            "price_amount": Decimal("0.00"),
            "estimated_retail_value": Decimal("14.00"),
            "triage_status": RoomScanItemDraft.TriageStatus.DONATE,
            "notes": "Suggested as a quick donation or free pickup item.",
        },
    ],
    RoomScanSession.RoomType.BATHROOM: [
        {
            "title": "Unopened toiletries",
            "category": Listing.Category.TOILETRIES,
            "condition": Listing.Condition.NEW,
            "price_type": Listing.PriceType.FREE,
            "price_amount": Decimal("0.00"),
            "estimated_retail_value": Decimal("16.00"),
            "triage_status": RoomScanItemDraft.TriageStatus.DONATE,
            "notes": "Suggested because sealed toiletries are great donation candidates.",
        },
        {
            "title": "Bathroom organizer",
            "category": Listing.Category.STORAGE,
            "condition": Listing.Condition.GOOD,
            "price_type": Listing.PriceType.FREE,
            "price_amount": Decimal("0.00"),
            "estimated_retail_value": Decimal("12.00"),
            "triage_status": RoomScanItemDraft.TriageStatus.SELL,
            "notes": "Suggested as a lightweight organizer another student can reuse fast.",
        },
    ],
}


def get_scan_session_queryset(user: UserType) -> QuerySet[RoomScanSession]:
    return (
        RoomScanSession.objects.filter(owner=user, is_demo=False)
        .select_related("owner")
        .prefetch_related(
            "images",
            "items__donation_hub",
            "items__linked_listing",
            "generated_listings",
            "tasks",
        )
    )


def get_task_queryset(user: UserType) -> QuerySet[MoveOutTask]:
    return MoveOutTask.objects.select_related("scan_session").filter(
        scan_session__owner=user,
        scan_session__is_demo=False,
    )


def build_scan_suggestion(scan_session: RoomScanSession) -> dict[str, Any]:
    templates = SCAN_SUGGESTIONS.get(scan_session.room_type) or SCAN_SUGGESTIONS[
        RoomScanSession.RoomType.DORM_ROOM
    ]
    suggestion = templates[scan_session.items.count() % len(templates)].copy()
    suggestion["title"] = f"{suggestion['title']} #{scan_session.items.count() + 1}"
    return suggestion


def serialize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        **summary,
        "estimated_student_savings": f"{summary['estimated_student_savings']:.2f}",
    }


def get_publish_candidates(scan_session: RoomScanSession) -> QuerySet[RoomScanItemDraft]:
    return scan_session.items.select_related(
        "scan_session",
        "source_image",
        "donation_hub",
        "linked_listing",
    ).filter(
        Q(
            triage_status__in=[
                RoomScanItemDraft.TriageStatus.SELL,
                RoomScanItemDraft.TriageStatus.DONATE,
            ]
        )
        | Q(linked_listing__isnull=False)
        | Q(donation_hub__isnull=False)
    )


def build_publish_queue_payload(scan_session: RoomScanSession, request: Request) -> dict[str, Any]:
    scan_session = (
        RoomScanSession.objects.select_related("owner")
        .prefetch_related(
            "images",
            "items__donation_hub",
            "items__linked_listing",
            "items__source_image",
            "generated_listings__owner",
        )
        .get(pk=scan_session.pk)
    )
    scan_session.refresh_progress(commit=True)
    items = list(get_publish_candidates(scan_session))
    serialized_items = list(RoomScanItemDraftSerializer(
        items,
        many=True,
        context={"request": request},
    ).data)
    readiness_order = {
        "ready": 0,
        "needs_info": 1,
        "donation_route": 2,
        "published": 3,
    }
    serialized_items.sort(
        key=lambda item: (
            readiness_order.get(item["publish_readiness"], 99),
            item["title"].lower(),
        )
    )
    summary = compute_scan_summary(scan_session)
    return {
        "session": RoomScanSessionSerializer(scan_session, context={"request": request}).data,
        "summary": serialize_summary(summary),
        "items": serialized_items,
        "published_listings": ListingSerializer(
            scan_session.generated_listings.select_related("owner", "source_scan_session"),
            many=True,
            context={"request": request},
        ).data,
    }


def ensure_listing_from_scan_item(
    item: RoomScanItemDraft,
    scan_session: RoomScanSession,
    price_type: Optional[str] = None,
) -> Listing:
    final_price_type = price_type or item.price_type or Listing.PriceType.FREE
    final_price_amount = Decimal("0.00")
    if final_price_type == Listing.PriceType.LOW_COST:
        final_price_amount = Decimal(item.price_amount or 0)
        if final_price_amount <= Decimal("0.00"):
            final_price_amount = max(
                Decimal("5.00"),
                (Decimal(item.estimated_retail_value or 0) * Decimal("0.35")).quantize(
                    Decimal("0.01")
                ),
            )

    defaults = {
        "title": item.title,
        "description": build_publish_description(item),
        "category": item.category,
        "condition": item.condition,
        "price_type": final_price_type,
        "price_amount": final_price_amount,
        "estimated_retail_value": item.estimated_retail_value,
        "pickup_zone": scan_session.pickup_zone or scan_session.room_label,
        "available_until": scan_session.move_out_deadline,
        "status": Listing.Status.AVAILABLE,
        "is_demo": scan_session.is_demo,
    }

    listing = item.linked_listing
    if listing is None:
        listing = Listing.objects.create(
            owner=scan_session.owner,
            source_scan_session=scan_session,
            **defaults,
        )
        item.linked_listing = listing
    else:
        for field, value in defaults.items():
            setattr(listing, field, value)
        listing.save()

    item.price_type = listing.price_type
    item.price_amount = listing.price_amount
    item.triage_status = RoomScanItemDraft.TriageStatus.DONE
    item.save(
        update_fields=[
            "linked_listing",
            "price_type",
            "price_amount",
            "triage_status",
            "updated_at",
        ]
    )
    return listing


def apply_draft_preset(item: RoomScanItemDraft, preset_key: str) -> Optional[dict[str, Any]]:
    preset = get_publish_preset(preset_key)
    if not preset:
        return None
    item.title = preset["default_title"]
    item.category = preset["category"]
    item.condition = preset["default_condition"]
    item.price_type = preset["default_price_type"]
    item.price_amount = preset["default_price_amount"]
    item.estimated_retail_value = preset["default_estimated_retail_value"]
    item.notes = preset["default_notes"]
    item.preset_key = preset_key
    return preset


def build_scan_board_payload(scan_session: RoomScanSession, request: Request) -> dict[str, Any]:
    scan_session = (
        RoomScanSession.objects.select_related("owner")
        .prefetch_related(
            "images",
            "items__donation_hub",
            "items__linked_listing",
            "items__source_image",
            "generated_listings__owner",
        )
        .get(pk=scan_session.pk)
    )
    scan_session.refresh_progress(commit=True)
    serialized_items = list(RoomScanItemDraftSerializer(
        scan_session.items.select_related("scan_session", "donation_hub", "linked_listing", "source_image"),
        many=True,
        context={"request": request},
    ).data)
    grouped: dict[str, list[Any]] = {key: [] for key in TRIAGE_ORDER}
    for item in serialized_items:
        grouped[item["triage_status"]].append(item)

    summary = compute_scan_summary(scan_session)
    return {
        "session": RoomScanSessionSerializer(scan_session, context={"request": request}).data,
        "summary": serialize_summary(summary),
        "columns": [
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "items": grouped[key],
            }
            for key in TRIAGE_ORDER
        ],
        "published_listings": ListingSerializer(
            scan_session.generated_listings.select_related("owner", "source_scan_session"),
            many=True,
            context={"request": request},
        ).data,
    }


def build_move_out_plan_payload(scan_session: RoomScanSession, request: Request) -> dict[str, Any]:
    scan_session = (
        RoomScanSession.objects.select_related("owner")
        .prefetch_related(
            "images",
            "items__donation_hub",
            "items__linked_listing",
            "items__source_image",
            "generated_listings__owner",
            "tasks",
        )
        .get(pk=scan_session.pk)
    )
    scan_session.refresh_progress(commit=True)
    from ..serializers import compute_task_summary
    summary = compute_scan_summary(scan_session)
    task_summary = compute_task_summary(scan_session)
    tasks = list(scan_session.tasks.all())
    tasks.sort(
        key=lambda task: (
            task.status == MoveOutTask.Status.DONE,
            task.due_at or timezone.now() + timedelta(days=365),
            task.created_at,
        )
    )
    critical_tasks = [
        task
        for task in tasks
        if task.status != MoveOutTask.Status.DONE
        and (task.is_overdue or (task.due_at and task.due_at <= timezone.now() + timedelta(hours=24)))
    ][:5]
    publish_candidates = [
        item
        for item in scan_session.items.select_related(
            "scan_session", "donation_hub", "linked_listing", "source_image"
        )
        if get_publish_readiness(item) == "ready"
    ][:4]
    blocked_candidates = [
        item
        for item in scan_session.items.select_related(
            "scan_session", "donation_hub", "linked_listing", "source_image"
        )
        if get_publish_readiness(item) == "needs_info"
    ][:4]
    return {
        "session": RoomScanSessionSerializer(scan_session, context={"request": request}).data,
        "summary": serialize_summary(summary),
        "task_summary": task_summary,
        "tasks": MoveOutTaskSerializer(tasks, many=True, context={"request": request}).data,
        "critical_tasks": MoveOutTaskSerializer(
            critical_tasks,
            many=True,
            context={"request": request},
        ).data,
        "ready_publish_items": RoomScanItemDraftSerializer(
            publish_candidates,
            many=True,
            context={"request": request},
        ).data,
        "blocked_publish_items": RoomScanItemDraftSerializer(
            blocked_candidates,
            many=True,
            context={"request": request},
        ).data,
    }


class RoomScanSessionListCreateView(generics.ListCreateAPIView):
    serializer_class = RoomScanSessionSerializer
    permission_classes = [permissions.IsAuthenticated, IsEmailVerified]

    def get_queryset(self) -> QuerySet[RoomScanSession]:
        return get_scan_session_queryset(current_user(self.request))

    def perform_create(self, serializer: Any) -> None:
        session = serializer.save(owner=self.request.user)
        seed_default_move_out_tasks(session)
        sync_system_move_out_tasks(session)


class RoomScanSessionDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = RoomScanSessionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[RoomScanSession]:
        return get_scan_session_queryset(current_user(self.request))

    def perform_update(self, serializer: Any) -> None:
        session = serializer.save()
        session.refresh_progress(commit=True)
        sync_system_move_out_tasks(session)


@extend_schema(exclude=True)
class RoomScanSessionPlanView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        return Response(build_move_out_plan_payload(scan_session, request))


class MoveOutTaskListCreateView(generics.ListCreateAPIView):
    serializer_class = MoveOutTaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[MoveOutTask]:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(self.request)), pk=self.kwargs["pk"])
        return scan_session.tasks.all()

    def perform_create(self, serializer: Any) -> None:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(self.request)), pk=self.kwargs["pk"])
        serializer.save(scan_session=scan_session, is_system=False)


class MoveOutTaskDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = MoveOutTaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[MoveOutTask]:
        return get_task_queryset(current_user(self.request))


class RoomScanImageUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        operation_id="scan_image_upload",
        request={"multipart/form-data": {"type": "object", "properties": {"images": {"type": "string", "format": "binary"}}}},
        responses={201: RoomScanSessionSerializer},
    )
    def post(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        files = request.FILES.getlist("images")
        if not files and request.FILES.get("image"):
            files = [request.FILES["image"]]

        if not files:
            return Response({"detail": "Upload at least one room photo."}, status=400)

        if scan_session.images.count() + len(files) > 4:
            return Response({"detail": "Each scan session is limited to 4 room photos."}, status=400)

        compressed = [compress_image(f, field_name="images") for f in files]

        starting_position = scan_session.images.count()
        for index, file in enumerate(compressed):
            RoomScanImage.objects.create(
                scan_session=scan_session,
                image=file,
                position=starting_position + index + 1,
            )

        scan_session.refresh_progress(commit=True)
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=scan_session.pk)
        return Response(
            RoomScanSessionSerializer(scan_session, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class RoomScanItemCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    @extend_schema(
        operation_id="scan_item_create",
        request=RoomScanItemDraftSerializer,
        responses={201: RoomScanItemDraftSerializer},
    )
    def post(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        payload = request.data.copy()
        source_image_id = payload.get("source_image")
        source_image: RoomScanImage | None
        if source_image_id:
            source_image = get_object_or_404(scan_session.images, pk=source_image_id)
        else:
            source_image = scan_session.images.first()

        if not source_image:
            return Response(
                {"detail": "Upload at least one room photo before creating rescue drafts."},
                status=400,
            )

        for key, value in build_scan_suggestion(scan_session).items():
            payload.setdefault(key, value)

        serializer = RoomScanItemDraftSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save(scan_session=scan_session, source_image=source_image)
        scan_session.refresh_progress(commit=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RoomScanItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = RoomScanItemDraftSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[RoomScanItemDraft]:
        return RoomScanItemDraft.objects.select_related(
            "scan_session",
            "donation_hub",
            "linked_listing",
            "source_image",
        ).filter(scan_session__owner=current_user(self.request), scan_session__is_demo=False)

    def perform_update(self, serializer: Any) -> None:
        item = serializer.save()
        item.scan_session.refresh_progress(commit=True)

    def perform_destroy(self, instance: Any) -> None:
        scan_session = instance.scan_session
        instance.delete()
        scan_session.refresh_progress(commit=True)


@extend_schema(exclude=True)
class RoomScanSessionBoardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        return Response(build_scan_board_payload(scan_session, request))


@extend_schema(exclude=True)
class PublishPresetsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(get_serialized_publish_presets())


@extend_schema(exclude=True)
class RoomScanSessionPublishQueueView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        return Response(build_publish_queue_payload(scan_session, request))


@extend_schema(exclude=True)
class RoomScanSessionBatchUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def patch(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        item_ids = request.data.get("item_ids") or []
        changes = request.data.get("changes") or {}

        if not item_ids:
            return Response({"detail": "Select at least one rescue draft first."}, status=400)
        if not changes:
            return Response({"detail": "Choose at least one field to update."}, status=400)

        items = list(
            scan_session.items.select_related(
                "scan_session",
                "donation_hub",
                "linked_listing",
                "source_image",
            ).filter(id__in=item_ids)
        )
        if len(items) != len(item_ids):
            return Response({"detail": "One or more selected drafts could not be found."}, status=400)

        session_changes = {}
        if "pickup_zone" in changes:
            session_changes["pickup_zone"] = changes["pickup_zone"]
        if "move_out_deadline" in changes:
            session_changes["move_out_deadline"] = changes["move_out_deadline"]

        if session_changes:
            session_serializer = RoomScanSessionSerializer(
                scan_session,
                data=session_changes,
                partial=True,
                context={"request": request},
            )
            session_serializer.is_valid(raise_exception=True)
            scan_session = session_serializer.save()

        allowed_item_fields = {
            "title",
            "category",
            "condition",
            "price_type",
            "price_amount",
            "estimated_retail_value",
            "triage_status",
            "notes",
            "preset_key",
        }
        item_changes = {key: value for key, value in changes.items() if key in allowed_item_fields}
        if not item_changes and not session_changes:
            return Response({"detail": "That batch edit field is not supported."}, status=400)

        preset_key = item_changes.pop("preset_key", None) if "preset_key" in item_changes else None
        if preset_key:
            if not get_publish_preset(preset_key):
                return Response({"detail": "That preset could not be found."}, status=400)

        for item in items:
            if preset_key:
                apply_draft_preset(item, preset_key)
            serializer = RoomScanItemDraftSerializer(
                item,
                data=item_changes,
                partial=True,
                context={"request": request},
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

        scan_session.refresh_progress(commit=True)
        return Response(build_publish_queue_payload(scan_session, request))


@extend_schema(exclude=True)
class RoomScanSessionPublishSelectedView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        item_ids = request.data.get("item_ids") or []

        if not item_ids:
            return Response({"detail": "Select at least one rescue draft first."}, status=400)

        if not scan_session.pickup_zone or not scan_session.move_out_deadline:
            return Response(
                {"detail": "Add a pickup zone and move-out deadline before publishing listings."},
                status=400,
            )

        items = list(
            scan_session.items.select_related(
                "scan_session",
                "donation_hub",
                "linked_listing",
                "source_image",
            ).filter(id__in=item_ids)
        )
        if len(items) != len(item_ids):
            return Response({"detail": "One or more selected drafts could not be found."}, status=400)

        blocked = []
        for item in items:
            readiness = get_publish_readiness(item)
            if readiness != "ready":
                blocked.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "publish_readiness": readiness,
                        "missing_fields": build_missing_fields(item),
                    }
                )

        if blocked:
            return Response(
                {
                    "detail": "Only publish-ready drafts can go live.",
                    "blocked_items": blocked,
                },
                status=400,
            )

        published_listing_ids = []
        for item in items:
            listing = ensure_listing_from_scan_item(item, scan_session, price_type=item.price_type)
            published_listing_ids.append(listing.id)

        scan_session.refresh_progress(commit=True)
        return Response(
            {
                "published_count": len(published_listing_ids),
                "published_listing_ids": published_listing_ids,
                "queue": build_publish_queue_payload(scan_session, request),
            }
        )


@extend_schema(exclude=True)
class RoomScanSessionBulkConvertView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(get_scan_session_queryset(current_user(request)), pk=pk)
        item_ids = request.data.get("item_ids") or []
        action = request.data.get("action")

        if not item_ids:
            return Response({"detail": "Select at least one rescue draft first."}, status=400)

        items = list(
            scan_session.items.select_related("linked_listing", "donation_hub").filter(id__in=item_ids)
        )
        if len(items) != len(item_ids):
            return Response({"detail": "One or more selected drafts could not be found."}, status=400)

        if action in {"create_free_listings", "create_low_cost_listings"} and (
            not scan_session.pickup_zone or not scan_session.move_out_deadline
        ):
            return Response(
                {"detail": "Add a pickup zone and move-out deadline before publishing listings."},
                status=400,
            )

        if action == "send_to_donation_hub":
            hub_id = request.data.get("donation_hub")
            donation_hub = get_object_or_404(DonationHub.objects.filter(active=True), pk=hub_id)
            for item in items:
                item.donation_hub = donation_hub
                item.triage_status = RoomScanItemDraft.TriageStatus.DONE
                item.save(update_fields=["donation_hub", "triage_status", "updated_at"])
        elif action in {"create_free_listings", "create_low_cost_listings"}:
            for item in items:
                price_type = (
                    Listing.PriceType.FREE
                    if action == "create_free_listings"
                    else Listing.PriceType.LOW_COST
                )
                ensure_listing_from_scan_item(item, scan_session, price_type=price_type)
        elif action == "mark_keep":
            for item in items:
                item.triage_status = RoomScanItemDraft.TriageStatus.KEEP
                item.save(update_fields=["triage_status", "updated_at"])
        elif action == "mark_toss":
            for item in items:
                item.triage_status = RoomScanItemDraft.TriageStatus.TOSS
                item.save(update_fields=["triage_status", "updated_at"])
        else:
            return Response({"detail": "That bulk action is not supported."}, status=400)

        scan_session.refresh_progress(commit=True)
        return Response(build_scan_board_payload(scan_session, request))


@extend_schema(exclude=True)
class AiDetectItemsView(APIView):
    """
    Kick off AI item detection for a scan image.

    The Claude Vision call can take 10–30 s, so it runs in a Celery task
    instead of blocking a web worker. In dev (no broker, eager mode) the task
    executes inline and the response already carries the finished result; in
    production the client polls the status endpoint.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AiDetectThrottle]

    def post(self, request: Request, pk: int) -> Response:
        import os
        if not ai_available():
            return Response(
                {"detail": "AI detection is not configured. Add GEMINI_API_KEY to the backend .env file."},
                status=503,
            )

        scan_session = get_object_or_404(RoomScanSession, pk=pk, owner=request.user, is_demo=False)
        image_id = request.data.get("image_id")
        if not image_id:
            return Response({"detail": "image_id is required."}, status=400)

        scan_image = get_object_or_404(RoomScanImage, pk=image_id, scan_session=scan_session)

        from django.core.cache import cache

        from ..tasks import ai_detect_cache_key, run_ai_detection

        key = ai_detect_cache_key(scan_session.pk, scan_image.pk)
        existing = cache.get(key)
        if existing and existing.get("status") == "processing":
            return Response({"status": "processing"}, status=202)

        cache.set(key, {"status": "processing"}, timeout=600)
        run_ai_detection.delay(scan_session.pk, scan_image.pk)

        # Eager mode (dev/tests) finishes inline — return the result directly
        # so the old synchronous contract still holds where there's no worker.
        result = cache.get(key) or {"status": "processing"}
        if result.get("status") == "done":
            scan_session.refresh_from_db()
            return Response({
                "status": "done",
                "created_count": result.get("created_count", 0),
                "session": RoomScanSessionSerializer(scan_session, context={"request": request}).data,
            }, status=201)
        if result.get("status") == "error":
            return Response({"detail": result.get("detail", "AI detection failed.")}, status=500)
        return Response({"status": "processing"}, status=202)


@extend_schema(exclude=True)
class AiDetectStatusView(APIView):
    """GET /scan-sessions/:id/ai-detect/status?image_id=N — poll detection state."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        scan_session = get_object_or_404(RoomScanSession, pk=pk, owner=request.user, is_demo=False)
        image_id = request.query_params.get("image_id")
        if not image_id:
            return Response({"detail": "image_id query param is required."}, status=400)

        from django.core.cache import cache

        from ..tasks import ai_detect_cache_key

        result = cache.get(ai_detect_cache_key(scan_session.pk, int(image_id)))
        if result is None:
            return Response({"status": "unknown"}, status=404)
        if result.get("status") == "done":
            return Response({
                "status": "done",
                "created_count": result.get("created_count", 0),
                "session": RoomScanSessionSerializer(scan_session, context={"request": request}).data,
            })
        if result.get("status") == "error":
            return Response({"status": "error", "detail": result.get("detail", "AI detection failed.")})
        return Response({"status": "processing"})
