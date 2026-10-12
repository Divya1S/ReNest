from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connection, transaction
from django.db.models import Count, Prefetch, Q, QuerySet, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers as drf_serializers
from rest_framework.parsers import FormParser, MultiPartParser
from dormcycle.parsers import StrictJSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..ai_client import (
    AiNotConfigured,
    ai_available,
    fetch_image_bytes,
    generate_json,
    generate_text,
    generate_vision_json,
    trim_to_sentence,
)
from ..models import Listing, ListingReport, ListingUpdate, ListingViewEvent, SavedListing
from ..permissions import IsEmailVerified, IsListingOwnerOrReadOnly
from ..throttles import (
    GenerateDescriptionThrottle,
    ListingCreateThrottle,
    ParseSearchThrottle,
    RescueSuggestionsThrottle,
    VisionAutofillThrottle,
)

from ..serializers import (
    ListingReportSerializer,
    ListingSerializer,
    ListingUpdateSerializer,
    SavedListingSerializer,
)
from dormcycle.typed import current_user

logger = logging.getLogger(__name__)

from rest_framework.exceptions import ValidationError

from .helpers import (
    LISTING_SERIALIZER_RELATIONS,
    annotate_listing_queryset,
    browse_listing_queryset,
    _expire_for_user,
    owner_campus_name,
    parse_int,
    parse_int_list,
    run_listing_created_hooks,
    user_can_post_update,
)

# Full-text search runs in the "english" configuration because that is what
# the GIN index from migration 0013 is built with. PostgreSQL only uses an
# expression index when the query repeats the indexed expression exactly, so
# the document below must stay in step with that migration: same config, same
# columns, same order.
_FTS_CONFIG = "english"


def _fts_document() -> SearchVector:
    """The indexed search document: title, description and pickup zone."""
    return SearchVector("title", "description", "pickup_zone", config=_FTS_CONFIG)


def _fts_rank_vector() -> Any:
    """Weighted vector used only to order rows the index already matched."""
    return (
        SearchVector("title", weight="A", config=_FTS_CONFIG)
        + SearchVector("description", weight="B", config=_FTS_CONFIG)
        + SearchVector("pickup_zone", weight="C", config=_FTS_CONFIG)
    )


class ListingListCreateView(generics.ListCreateAPIView):
    serializer_class = ListingSerializer
    # Read is public so guests can browse the marketplace; creating listings
    # still requires a signed-in, email-verified account.
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsEmailVerified]
    parser_classes = [MultiPartParser, FormParser, StrictJSONParser]
    throttle_classes = [ListingCreateThrottle]

    def get_queryset(self) -> QuerySet[Listing]:
        user = current_user(self.request)
        is_authed = user.is_authenticated
        params = self.request.query_params

        mine = is_authed and params.get("mine") == "1"
        if mine:
            # The owner's own shelf: every status, flagged and expired included.
            _expire_for_user(user)
            queryset = Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS).filter(
                owner=user, is_demo=False
            )
        else:
            # Public browse is the shared marketplace queryset, so what it
            # shows cannot drift from search, the map or the concierge.
            # Two things are specific to this endpoint: staff also see
            # flagged listings, and ?campus=slug replaces the viewer's own
            # campus scope for guests (public campus pages), staff and
            # campus managers.
            campus_override = params.get("campus")
            may_override = not is_authed or user.is_staff or user.is_campus_manager
            queryset = browse_listing_queryset(
                user if is_authed else None,
                include_flagged=bool(is_authed and user.is_staff),
                campus_slug=campus_override if may_override else None,
            )
        queryset = annotate_listing_queryset(queryset, user if is_authed else None)

        category = params.get("category")
        if category:
            queryset = queryset.filter(category=category)

        price_type = params.get("price_type")
        if price_type:
            queryset = queryset.filter(price_type=price_type)

        # Public browse is already limited to live listings by the shared
        # queryset; the status filter narrows within that (or, with mine=1,
        # within everything the owner has).
        status_param = params.get("status")
        if status_param == "active":
            queryset = queryset.filter(
                status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED]
            )
        elif status_param:
            if status_param not in Listing.Status.values:
                raise ValidationError({"status": "Unknown listing status."})
            queryset = queryset.filter(status=status_param)

        scan_session_id = params.get("source_scan_session")
        if scan_session_id:
            queryset = queryset.filter(
                source_scan_session_id=parse_int(scan_session_id, field="source_scan_session", minimum=1)
            )

        building = params.get("building")
        if building:
            queryset = queryset.filter(building__iexact=building)

        sort = params.get("sort", "deadline")
        search = (params.get("search") or "").strip()
        use_fts = bool(search) and connection.vendor == "postgresql"
        if search:
            if use_fts:
                query = SearchQuery(search, config=_FTS_CONFIG, search_type="websearch")
                # Match with @@ on the indexed expression. The old filter
                # (rank > 0 over unconfigured, weighted vectors) could not
                # use the index and built a tsvector for every row. alias()
                # keeps the document out of SELECT and GROUP BY.
                queryset = queryset.alias(_fts_document=_fts_document()).filter(_fts_document=query)
                if sort == "relevance":
                    queryset = queryset.annotate(rank=SearchRank(_fts_rank_vector(), query))
            else:
                queryset = queryset.filter(
                    Q(title__icontains=search)
                    | Q(description__icontains=search)
                    | Q(pickup_zone__icontains=search)
                )

        # Phase 17: active boosts surface first (NULLS LAST via conditional annotation)
        from django.db.models import BooleanField, ExpressionWrapper, Q as _Q
        from django.utils import timezone as _tz
        now = _tz.now()
        queryset = queryset.annotate(
            is_boosted=ExpressionWrapper(
                _Q(boosted_until__gt=now),
                output_field=BooleanField(),
            )
        )

        if sort == "relevance" and use_fts:
            return queryset.order_by("-is_boosted", "-rank", "available_until")  # rank annotated above
        if sort == "newest":
            return queryset.order_by("-is_boosted", "-created_at")
        if sort == "price_low":
            return queryset.order_by("-is_boosted", "price_amount", "available_until")
        if sort == "price_high":
            return queryset.order_by("-is_boosted", "-price_amount", "available_until")
        # Default: deadline first, then completeness.
        # Personalised: if user has category_affinity, preferred categories bubble up.
        # Done as a single annotated ORDER BY so pagination stays in SQL;
        # never materialise the whole queryset in Python.
        affinity: dict = getattr(user, "category_affinity", {}) or {}
        if affinity and not mine:
            top_category = max(affinity, key=affinity.__getitem__)
            queryset = queryset.annotate(
                is_preferred=ExpressionWrapper(
                    _Q(category=top_category),
                    output_field=BooleanField(),
                )
            )
            return queryset.order_by("-is_preferred", "-is_boosted", "available_until", "-completeness_score_ann", "-created_at")  # annotated in helper
        return queryset.order_by("-is_boosted", "available_until", "-completeness_score_ann", "-created_at")  # annotated in helper

    def perform_create(self, serializer: Any) -> None:
        listing = serializer.save(owner=self.request.user)
        run_listing_created_hooks(listing)


class ListingDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ListingSerializer
    # Read is public (shareable listing URLs); edit/delete stays owner-only.
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsListingOwnerOrReadOnly]
    parser_classes = [MultiPartParser, FormParser, StrictJSONParser]

    def get_queryset(self) -> QuerySet[Listing]:
        qs = Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS).exclude(is_demo=True)
        user = current_user(self.request)
        # A flagged listing stays reachable for its owner (so they can see the
        # moderation notice) and for staff, but not by direct link for anyone else.
        if not (user.is_authenticated and user.is_staff):
            visible = ~Q(moderation_status=Listing.ModerationStatus.FLAGGED)
            if user.is_authenticated:
                visible |= Q(owner_id=user.id)
            qs = qs.filter(visible)
        return annotate_listing_queryset(qs, user if user.is_authenticated else None)

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        instance = self.get_object()
        instance.refresh_status(commit=True)
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key
        ListingViewEvent.objects.get_or_create(listing=instance, session_key=session_key)
        if request.user.is_authenticated:
            self._record_view_interaction(current_user(request), instance)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @staticmethod
    def _record_view_interaction(user: Any, listing: Listing) -> None:
        """Log one VIEW per user, listing and day for personalised re-ranking.

        A GET must not grow the table without bound: every refresh used to
        insert a row and then COUNT all of the user's rows. The unique
        constraint on (user, listing, action, day) makes get_or_create safe
        under concurrency, and the count now only runs when a row was added.
        """
        from ..models import ListingInteractionEvent

        _, created = ListingInteractionEvent.objects.get_or_create(
            user=user,
            listing=listing,
            action=ListingInteractionEvent.Action.VIEW,
            day=timezone.localdate(),
        )
        if not created:
            return
        total = ListingInteractionEvent.objects.filter(user=user).count()
        if total % 10 == 0:
            try:
                from ..tasks import recompute_category_affinity_task

                recompute_category_affinity_task.delay(user.pk)
            except Exception:
                logger.exception("category affinity recompute could not be scheduled for user %s", user.pk)

    @staticmethod
    def _moderated_content(listing: Listing) -> tuple[Any, ...]:
        """The fields moderation screens: public text, price and cover image."""
        return (
            listing.title,
            listing.description,
            listing.category,
            listing.price_type,
            listing.price_amount,
            listing.image.name if listing.image else "",
            listing.image_cdn_url,
        )

    def perform_update(self, serializer: Any) -> None:
        before = self._moderated_content(serializer.instance)
        listing = serializer.save()
        if self._moderated_content(listing) == before:
            return
        # Moderation ran once at creation; an owner could post something
        # clean and edit it afterwards. The task decides what a re-screen may
        # change (it never lifts a moderator's flag). A scheduling failure
        # must never fail the save that already happened.
        try:
            from ..tasks import remoderate_listing_task

            remoderate_listing_task.delay(listing.pk)
        except Exception:
            logger.exception("re-moderation could not be scheduled for listing %s", listing.pk)
            return
        # Without a worker the task has already run inline; show its verdict.
        listing.refresh_from_db(fields=["moderation_status", "moderation_flag_reason"])

    def perform_destroy(self, instance: Any) -> None:
        from ..services import release_listing_claims
        from .dashboard import invalidate_user_dashboard_cache

        with transaction.atomic():
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted_at", "updated_at"])
            # A deleted listing must not keep a live claim pointing at it.
            release_listing_claims(
                instance, actor=self.request.user, reason="The owner removed the listing."
            )
        invalidate_user_dashboard_cache(instance.owner_id)


MAX_GALLERY_IMAGES = 4  # extra photos beyond the cover image


class ListingGalleryUploadView(APIView):
    """POST /listings/:id/images: owner adds up to 4 extra gallery photos."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request: Request, pk: int) -> Response:
        listing = get_object_or_404(
            Listing.objects.exclude(is_demo=True), pk=pk, owner=current_user(request)
        )
        files = request.FILES.getlist("images")
        if not files:
            return Response(
                {"detail": "Attach at least one image under the 'images' field."}, status=400
            )
        existing = listing.images.count()
        if existing + len(files) > MAX_GALLERY_IMAGES:
            return Response(
                {
                    "detail": (
                        f"A listing can have at most {MAX_GALLERY_IMAGES} extra photos "
                        f"(you already have {existing})."
                    )
                },
                status=400,
            )

        from ..image_utils import compress_image
        from ..models import ListingImage

        for offset, uploaded in enumerate(files):
            compressed = compress_image(uploaded)  # ValidationError → DRF 400
            ListingImage.objects.create(
                listing=listing, image=compressed, position=existing + offset
            )

        serializer = ListingSerializer(listing, context={"request": request})
        return Response({"listing": serializer.data}, status=201)


class ListingGalleryImageView(APIView):
    """DELETE /listings/:id/images/:image_id: owner removes a gallery photo."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request: Request, pk: int, image_id: int) -> Response:
        listing = get_object_or_404(
            Listing.objects.exclude(is_demo=True), pk=pk, owner=current_user(request)
        )
        from ..models import ListingImage

        deleted, _ = ListingImage.objects.filter(pk=image_id, listing=listing).delete()
        if not deleted:
            return Response({"detail": "Image not found."}, status=404)
        serializer = ListingSerializer(listing, context={"request": request})
        return Response({"listing": serializer.data})


class ListingUpdateListCreateView(generics.ListCreateAPIView):
    serializer_class = ListingUpdateSerializer
    # Coordination updates are readable on the public listing page; posting
    # remains restricted to the owner or a claimant (checked in perform_create).
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_listing(self) -> Listing:
        listing = get_object_or_404(
            Listing.objects.exclude(is_demo=True).select_related("owner"),
            pk=self.kwargs["pk"],
        )
        listing.refresh_status(commit=True)
        return listing

    def get_queryset(self) -> QuerySet[ListingUpdate]:
        listing = self.get_listing()
        return listing.updates.select_related("author", "listing")

    def perform_create(self, serializer: Any) -> None:
        listing = self.get_listing()
        if not user_can_post_update(current_user(self.request), listing):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only the owner or a claimant can post updates.")
        serializer.save(listing=listing, author=self.request.user)


class SavedListingListView(generics.ListAPIView):
    serializer_class = SavedListingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[SavedListing]:
        user = current_user(self.request)
        return (
            SavedListing.objects.filter(
                user=user, listing__is_demo=False, listing__deleted_at__isnull=True
            )
            .prefetch_related(
                Prefetch(
                    "listing",
                    queryset=annotate_listing_queryset(
                        Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS), user
                    ),
                )
            )
            .order_by("-created_at")
        )


_SavedToggleResponse = inline_serializer(
    "SavedToggleResponse",
    fields={"saved": drf_serializers.BooleanField(), "listing": ListingSerializer()},
)


class SavedListingToggleView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(operation_id="listing_save", request=None, responses={200: _SavedToggleResponse, 201: _SavedToggleResponse})
    def post(self, request: Request, pk: int) -> Response:
        listing = get_object_or_404(Listing.objects.exclude(is_demo=True), pk=pk)
        if listing.owner_id == request.user.id:
            return Response(
                {"detail": "You do not need to save your own listing."},
                status=400,
            )

        _, created = SavedListing.objects.get_or_create(user=current_user(request), listing=listing)
        serializer = ListingSerializer(listing, context={"request": request})
        return Response(
            {"saved": True, "created": created, "listing": serializer.data},
            status=201 if created else 200,
        )

    @extend_schema(operation_id="listing_unsave", request=None, responses={200: _SavedToggleResponse})
    def delete(self, request: Request, pk: int) -> Response:
        listing = get_object_or_404(Listing.objects.exclude(is_demo=True), pk=pk)
        SavedListing.objects.filter(user=current_user(request), listing=listing).delete()
        serializer = ListingSerializer(listing, context={"request": request})
        return Response({"saved": False, "listing": serializer.data})


class ListingReportListCreateView(generics.ListCreateAPIView):
    serializer_class = ListingReportSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_listing(self) -> Listing:
        return get_object_or_404(
            Listing.objects.select_related("owner").exclude(is_demo=True),
            pk=self.kwargs["pk"],
        )

    def get_queryset(self) -> QuerySet[ListingReport]:
        listing = self.get_listing()
        queryset = listing.reports.select_related("listing", "reporter")
        # Only moderators see other people's reports: showing an owner who
        # reported them turns a safety report into a retaliation target.
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(reporter=current_user(self.request))

    def perform_create(self, serializer: Any) -> None:
        listing = self.get_listing()
        serializer.save(listing=listing, reporter=self.request.user)


@extend_schema(
    operation_id="listings_bulk_update",
    request=inline_serializer(
        "ListingBulkUpdateRequest",
        fields={
            "pickup_zone": drf_serializers.CharField(required=False, max_length=140),
            "available_until": drf_serializers.DateTimeField(required=False),
            "listing_ids": drf_serializers.ListField(
                child=drf_serializers.IntegerField(), required=False
            ),
        },
    ),
    responses={
        200: inline_serializer(
            "ListingBulkUpdateResponse",
            fields={
                "updated": drf_serializers.IntegerField(),
                "listings": ListingSerializer(many=True),
            },
        )
    },
)
class ListingBulkUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def patch(self, request: Request) -> Response:
        pickup_zone = request.data.get("pickup_zone")
        available_until = request.data.get("available_until")
        listing_ids = request.data.get("listing_ids")

        if pickup_zone is None and available_until is None:
            return Response(
                {"detail": "Provide at least one field to update: pickup_zone or available_until."},
                status=400,
            )

        if available_until is not None:
            serializer = drf_serializers.DateTimeField()
            try:
                available_until = serializer.to_internal_value(available_until)
            except drf_serializers.ValidationError as exc:
                return Response({"available_until": exc.detail}, status=400)
            if available_until <= timezone.now():
                return Response(
                    {"available_until": "Move-out deadline must be in the future."},
                    status=400,
                )

        active_statuses = [Listing.Status.AVAILABLE, Listing.Status.RESERVED]
        queryset = Listing.objects.filter(
            owner=current_user(request),
            is_demo=False,
            status__in=active_statuses,
        )

        if listing_ids is not None:
            listing_ids = parse_int_list(listing_ids, field="listing_ids", max_items=200)
            queryset = queryset.filter(id__in=listing_ids)
            if queryset.count() != len(set(listing_ids)):
                return Response(
                    {"listing_ids": "One or more listing IDs are not your active listings."},
                    status=400,
                )

        # queryset.update() runs no model validation, so the values are
        # validated here against the column they are written to. The old
        # limit of 160 was longer than the column (140): PostgreSQL rejected
        # such a value with a DataError, which surfaced as a 500.
        update_fields: dict[str, Any] = {"updated_at": timezone.now()}
        if pickup_zone is not None:
            if not isinstance(pickup_zone, str):
                return Response({"pickup_zone": "Must be text."}, status=400)
            zone_field = drf_serializers.CharField(
                max_length=Listing._meta.get_field("pickup_zone").max_length or 140,
                allow_blank=False,
                trim_whitespace=True,
            )
            try:
                update_fields["pickup_zone"] = zone_field.run_validation(pickup_zone)
            except drf_serializers.ValidationError as exc:
                return Response({"pickup_zone": exc.detail}, status=400)
        if available_until is not None:
            update_fields["available_until"] = available_until

        updated_count = queryset.update(**update_fields)

        refreshed_qs = annotate_listing_queryset(
            Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS).filter(
                owner=current_user(request), is_demo=False, status__in=active_statuses
            ),
            current_user(request),
        ).order_by("available_until", "-created_at")

        return Response(
            {
                "updated": updated_count,
                "listings": ListingSerializer(
                    refreshed_qs, many=True, context={"request": request}
                ).data,
            }
        )


# ---------------------------------------------------------------------------
# AI helpers
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "storage": "storage / organizers",
    "lighting": "lighting",
    "supplies": "school supplies",
    "comfort": "comfort / bedding",
    "toiletries": "toiletries",
    "decor": "decor",
    "other": "general item",
}

_CONDITION_LABELS = {
    "new": "like new",
    "good": "good condition",
    "fair": "fair / used condition",
}


def _generate_copy(prompt: str, max_tokens: int = 300) -> str:
    # Raises AiNotConfigured (a RuntimeError) when no Gemini key is present,
    # which callers translate into a 503.
    return generate_text(prompt, max_tokens=max_tokens).strip()


@extend_schema(
    operation_id="listings_generate_description",
    request=inline_serializer(
        "GenerateDescriptionRequest",
        fields={
            "title": drf_serializers.CharField(),
            "category": drf_serializers.CharField(),
            "condition": drf_serializers.CharField(),
            "price_type": drf_serializers.CharField(required=False),
            "estimated_retail_value": drf_serializers.DecimalField(
                max_digits=7, decimal_places=2, required=False
            ),
        },
    ),
    responses={
        200: inline_serializer(
            "GenerateDescriptionResponse",
            fields={"description": drf_serializers.CharField()},
        )
    },
)
class GenerateDescriptionView(APIView):
    """Ask the AI model to draft a listing description from title + metadata."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]
    throttle_classes = [GenerateDescriptionThrottle]

    def post(self, request: Request) -> Response:
        # Everything here ends up in a prompt, so it is coerced to short text
        # first; a list or a 50 kB "title" must not reach the model.
        title = str(request.data.get("title", "")).strip()[:140]
        category = str(request.data.get("category", "other"))[:24]
        condition = str(request.data.get("condition", "good"))[:24]
        price_type = str(request.data.get("price_type", "free"))[:24]
        retail = request.data.get("estimated_retail_value")

        if not title:
            return Response({"detail": "title is required."}, status=400)

        retail_value = Decimal("0")
        if retail not in (None, ""):
            try:
                retail_value = Decimal(str(retail))
            except InvalidOperation:
                return Response({"estimated_retail_value": "Must be a number."}, status=400)
            if not retail_value.is_finite():
                return Response({"estimated_retail_value": "Must be a number."}, status=400)

        category_label = _CATEGORY_LABELS.get(category, "item")
        condition_label = _CONDITION_LABELS.get(condition, "used")
        retail_line = (
            f"Original retail value: approximately ${retail_value:.0f}." if retail_value > 0 else ""
        )
        price_line = "This item is being offered for free." if price_type == "free" else "This item is low-cost."

        prompt = (
            f"Write a short, honest listing description for a college student selling a dorm item during move-out.\n\n"
            f"Item: {title}\n"
            f"Category: {category_label}\n"
            f"Condition: {condition_label}\n"
            f"{retail_line}\n"
            f"{price_line}\n\n"
            f"Guidelines:\n"
            f"- 2–4 sentences maximum\n"
            f"- Mention condition honestly\n"
            f"- Mention what the next student gets out of it\n"
            f"- No marketing fluff, no emojis, no price information\n"
            f"- Plain prose only, no bullet points\n\n"
            f"Write only the description text, nothing else."
        )

        try:
            description = trim_to_sentence(_generate_copy(prompt))
        except AiNotConfigured as exc:
            return Response({"detail": str(exc)}, status=503)
        except Exception as exc:
            logger.exception("generate-description failed")
            detail = "AI service unavailable. Please write a description manually."
            if settings.DEBUG:
                detail = f"{detail} ({type(exc).__name__}: {exc})"
            return Response({"detail": detail}, status=503)

        return Response({"description": description})


@extend_schema(
    operation_id="listings_pricing_hint",
    responses={
        200: inline_serializer(
            "PricingHintResponse",
            fields={
                "sample_size": drf_serializers.IntegerField(),
                "free_pct": drf_serializers.IntegerField(),
                "median_price": drf_serializers.DecimalField(
                    max_digits=7, decimal_places=2, allow_null=True
                ),
                "price_range": inline_serializer(
                    "PriceRange",
                    fields={
                        "low": drf_serializers.DecimalField(max_digits=7, decimal_places=2),
                        "high": drf_serializers.DecimalField(max_digits=7, decimal_places=2),
                    },
                    allow_null=True,
                ),
                "suggestion": drf_serializers.CharField(),
            },
        )
    },
)
class PricingHintView(APIView):
    """Return median price / free % for a given category + condition from completed pickups."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        category = request.query_params.get("category", "")
        condition = request.query_params.get("condition", "")

        if not category or not condition:
            return Response(
                {"detail": "category and condition query params are required."}, status=400
            )

        qs = (
            Listing.objects.filter(
                status=Listing.Status.PICKED_UP,
                is_demo=False,
                category=category,
                condition=condition,
            )
            .order_by("-updated_at")[:60]
        )

        records = list(qs.values("price_type", "price_amount"))
        sample_size = len(records)

        if sample_size == 0:
            return Response(
                {
                    "sample_size": 0,
                    "free_pct": None,
                    "median_price": None,
                    "price_range": None,
                    "suggestion": "No comparable sales yet. Set a price that feels fair to you.",
                }
            )

        free_count = sum(1 for r in records if r["price_type"] == "free")
        free_pct = round(free_count / sample_size * 100)
        paid = sorted(
            float(r["price_amount"]) for r in records if r["price_type"] == "low_cost" and float(r["price_amount"]) > 0
        )

        if paid:
            mid = len(paid) // 2
            median_price = Decimal(str(paid[mid] if len(paid) % 2 else (paid[mid - 1] + paid[mid]) / 2)).quantize(Decimal("0.01"))
            price_range = {
                "low": Decimal(str(paid[0])).quantize(Decimal("0.01")),
                "high": Decimal(str(paid[-1])).quantize(Decimal("0.01")),
            }
        else:
            median_price = None
            price_range = None

        if free_pct >= 70:
            suggestion = f"{free_pct}% of similar items go free. Consider making yours free too."
        elif median_price:
            suggestion = f"Similar items typically sell for around ${median_price:.0f} ({free_pct}% go free)."
        else:
            suggestion = f"{free_pct}% of similar items go free."

        return Response(
            {
                "sample_size": sample_size,
                "free_pct": free_pct,
                "median_price": median_price,
                "price_range": price_range,
                "suggestion": suggestion,
            }
        )


@extend_schema(
    operation_id="listings_parse_search",
    request=inline_serializer(
        "ParseSearchRequest",
        fields={"query": drf_serializers.CharField()},
    ),
    responses={
        200: inline_serializer(
            "ParseSearchResponse",
            fields={
                "search": drf_serializers.CharField(allow_null=True),
                "category": drf_serializers.CharField(allow_null=True),
                "price_type": drf_serializers.CharField(allow_null=True),
                "pickup_zone_hint": drf_serializers.CharField(allow_null=True),
                "chips": drf_serializers.ListField(child=drf_serializers.DictField()),
            },
        )
    },
)
class ParseSearchView(APIView):
    """
    Parse a natural-language search query into structured filter params via the AI model.
    Example: "storage bins under $10 near north dorms"
      → { search: "storage bins", category: "storage", price_type: "low_cost", pickup_zone_hint: "north dorms" }
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]
    throttle_classes = [ParseSearchThrottle]

    # Derived from the model so a new category cannot be forgotten here; the
    # hand-written set had already fallen behind (it rejected "decor").
    _VALID_CATEGORIES = frozenset(Listing.Category.values)
    _VALID_PRICE_TYPES = frozenset(Listing.PriceType.values)
    _MAX_QUERY_CHARS = 200

    def post(self, request: Request) -> Response:
        query = str(request.data.get("query", "")).strip()[: self._MAX_QUERY_CHARS]
        if not query:
            return Response({"detail": "query is required."}, status=400)

        categories = json.dumps(list(Listing.Category.values), separators=(",", ":"))
        prompt = (
            "You are a search query parser for a campus marketplace called ReNest where "
            "college students list dorm items during move-out. Parse the user's natural language "
            "search query into structured filter parameters.\n\n"
            f"User query: {json.dumps(query)}\n\n"
            "Extract the following fields and respond ONLY with a JSON object, no other text:\n"
            '- "search": the core keyword(s) for text search (strip price/location qualifiers); null if none\n'
            f'- "category": one of {categories} or null\n'
            '- "price_type": "free" if user wants free items, "low_cost" if under a dollar amount / cheap / low cost, null if no price constraint\n'
            '- "pickup_zone_hint": location string the user mentioned (e.g. "north dorms", "building A"); null if none\n\n'
            "Examples:\n"
            '"storage bins under $10 near north dorms" → {"search":"storage bins","category":"storage","price_type":"low_cost","pickup_zone_hint":"north dorms"}\n'
            '"free lamp for my room" → {"search":"lamp","category":"lighting","price_type":"free","pickup_zone_hint":null}\n'
            '"textbooks" → {"search":"textbooks","category":"supplies","price_type":null,"pickup_zone_hint":null}\n\n'
            "Respond with only the JSON object."
        )

        # generate_json asks the model for application/json and parses the
        # answer with the shared fence- and prose-tolerant parser, replacing
        # the regex this view used to carry for the same job.
        try:
            parsed = generate_json(prompt, max_tokens=150)
        except AiNotConfigured as exc:
            return Response({"detail": str(exc)}, status=503)
        except Exception:
            logger.exception("parse-search failed")
            return Response({"detail": "AI service unavailable."}, status=503)

        if not isinstance(parsed, dict):
            return Response({"detail": "Could not parse AI response."}, status=502)

        def _text(value: Any) -> str | None:
            # The model is asked for strings but may return anything.
            if not isinstance(value, str):
                return None
            return value.strip()[:200] or None

        search = _text(parsed.get("search"))
        raw_category = _text(parsed.get("category"))
        raw_price_type = _text(parsed.get("price_type"))
        category = raw_category if raw_category in self._VALID_CATEGORIES else None
        price_type = raw_price_type if raw_price_type in self._VALID_PRICE_TYPES else None
        pickup_zone_hint = _text(parsed.get("pickup_zone_hint"))

        chips: list[dict] = []
        if category:
            chips.append({"label": f"Category: {category.capitalize()}", "field": "category", "value": category})
        if price_type:
            chips.append({"label": "Free" if price_type == "free" else "Low cost", "field": "price_type", "value": price_type})
        if pickup_zone_hint:
            chips.append({"label": f"Near: {pickup_zone_hint}", "field": "pickup_zone_hint", "value": pickup_zone_hint})

        return Response(
            {
                "search": search,
                "category": category,
                "price_type": price_type,
                "pickup_zone_hint": pickup_zone_hint,
                "chips": chips,
            }
        )


@extend_schema(
    operation_id="listing_repost",
    request=inline_serializer(
        "RepostRequest",
        fields={"token": drf_serializers.CharField()},
    ),
    responses={
        200: inline_serializer(
            "RepostResponse",
            fields={
                "listing": ListingSerializer(),
                "extended_days": drf_serializers.IntegerField(),
            },
        )
    },
)
class RepostListingView(APIView):
    """
    Extend an expired listing by 7 days using the signed repost token
    issued when the listing first expired.  Requires the owner to be
    authenticated; the token is an additional tamper-proof guard.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    _EXTEND_DAYS = 7

    def post(self, request: Request, pk: int) -> Response:
        from ..services import release_listing_claims

        with transaction.atomic():
            # Listing.objects, not all_objects: a soft-deleted listing must
            # not be revivable through a leftover token.
            listing = get_object_or_404(
                Listing.objects.select_for_update(),
                pk=pk,
                owner=request.user,
                is_demo=False,
            )

            token = request.data.get("token", "")
            if (
                not listing.repost_token
                or not secrets.compare_digest(str(listing.repost_token).encode(), str(token).encode())
            ):
                return Response({"detail": "Invalid or missing repost token."}, status=400)

            if listing.repost_token_expires_at and listing.repost_token_expires_at < timezone.now():
                return Response({"detail": "Repost token has expired. Go to My Listings to edit and re-save."}, status=400)

            if listing.status not in {Listing.Status.EXPIRED, Listing.Status.AVAILABLE}:
                return Response(
                    {"detail": "Only expired or available listings can be reposted."},
                    status=400,
                )

            # A listing can expire with a requested or confirmed claim still
            # attached. Relisting it as AVAILABLE while that claim stays live
            # would show an item nobody else can reserve.
            release_listing_claims(listing, actor=request.user, reason="The owner relisted the item.")

            listing.available_until = timezone.now() + timedelta(days=self._EXTEND_DAYS)
            listing.status = Listing.Status.AVAILABLE
            listing.repost_token = None
            listing.repost_token_expires_at = None
            listing.save(update_fields=["available_until", "status", "repost_token", "repost_token_expires_at", "updated_at"])

        refreshed = annotate_listing_queryset(
            Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS).filter(pk=listing.pk),
            current_user(request),
        ).first()

        return Response(
            {
                "listing": ListingSerializer(refreshed, context={"request": request}).data,
                "extended_days": self._EXTEND_DAYS,
            }
        )


class DonateListingView(APIView):
    """
    Mark a listing as donated to a hub.  Generates a PDF receipt, stores it
    on the listing, and emails it to the owner.
    POST /api/listings/:id/donate  { "hub_id": 3 }  (hub_id optional)
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def post(self, request: Request, pk: int) -> Response:
        from ..receipts import generate_donation_receipt
        from ..services import release_listing_claims

        hub = None
        hub_id = parse_int(request.data.get("hub_id"), field="hub_id", minimum=1)
        if hub_id is not None:
            from hubs.models import DonationHub

            hub = get_object_or_404(DonationHub, pk=hub_id, active=True)

        # One transaction: the status flip, the cancelled claims and the
        # stored receipt either all happen or none do. Before, a failure
        # after the first save left a DONATED listing with a live claim.
        with transaction.atomic():
            listing = get_object_or_404(
                Listing.objects.select_for_update(),
                pk=pk,
                owner=request.user,
                is_demo=False,
            )
            if listing.status not in {Listing.Status.AVAILABLE, Listing.Status.RESERVED, Listing.Status.EXPIRED}:
                return Response({"detail": "Only available, reserved, or expired listings can be donated."}, status=400)

            if hub is not None:
                listing.donation_hub = hub
            listing.status = Listing.Status.DONATED
            listing.save(update_fields=["status", "donation_hub", "updated_at"])

            # The item is going to a hub, so nobody can still be promised it.
            release_listing_claims(
                listing, actor=request.user, reason="The owner donated the item to a campus hub."
            )

            receipt = generate_donation_receipt(listing)
            if receipt:
                listing.donation_receipt.save(receipt.name or "receipt.pdf", receipt, save=True)

        # Notifications go out only after the donation is committed, and a
        # failure in one of them must not turn a saved donation into a 500.
        try:
            from ..tasks import send_donation_receipt_email_task

            send_donation_receipt_email_task.delay(listing.pk)
        except Exception:
            logger.exception("donation receipt email could not be scheduled for listing %s", listing.pk)

        from .dashboard import invalidate_user_dashboard_cache
        invalidate_user_dashboard_cache(request.user.pk)

        try:
            from .partner import dispatch_webhook_event

            dispatch_webhook_event(
                "listing_donated",
                {"id": listing.pk, "title": listing.title, "hub_id": hub_id},
                owner_campus_name(listing.owner),
            )
        except Exception:
            logger.exception("listing_donated webhook dispatch failed for listing %s", listing.pk)

        return Response({"status": "donated", "receipt_generated": receipt is not None})


@extend_schema(
    operation_id="listing_analytics",
    responses={
        200: inline_serializer(
            "ListingAnalyticsResponse",
            fields={
                "total_views": drf_serializers.IntegerField(),
                "total_saves": drf_serializers.IntegerField(),
                "total_reservations": drf_serializers.IntegerField(),
                "days_active": drf_serializers.IntegerField(),
                "conversion_rate": drf_serializers.FloatField(),
                "views_by_day": drf_serializers.ListField(
                    child=inline_serializer(
                        "ViewsByDay",
                        fields={
                            "date": drf_serializers.DateField(),
                            "views": drf_serializers.IntegerField(),
                        },
                    )
                ),
            },
        )
    },
)
class ListingAnalyticsView(APIView):
    """Per-listing analytics for the owner: views/day, saves, reservations, conversion."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        listing = get_object_or_404(
            Listing.objects.select_related("owner").exclude(is_demo=True),
            pk=pk,
            owner=request.user,
        )

        total_views = listing.view_events.count()

        views_by_day = list(
            listing.view_events
            .annotate(date=TruncDate("viewed_at"))
            .values("date")
            .annotate(views=Count("id"))
            .order_by("date")
        )

        total_saves = SavedListing.objects.filter(listing=listing).count()
        total_reservations = listing.reservations.count()

        now = timezone.now()
        days_active = max(1, (now - listing.created_at).days)
        conversion_rate = round(total_reservations / total_views, 4) if total_views else 0.0

        return Response(
            {
                "total_views": total_views,
                "total_saves": total_saves,
                "total_reservations": total_reservations,
                "days_active": days_active,
                "conversion_rate": conversion_rate,
                "views_by_day": [
                    {"date": str(row["date"]), "views": row["views"]}
                    for row in views_by_day
                ],
            }
        )


# ---------------------------------------------------------------------------
# AI Intelligence v2
# ---------------------------------------------------------------------------

@extend_schema(
    operation_id="listings_vision_autofill",
    request=inline_serializer(
        "VisionAutofillRequest",
        fields={"image_url": drf_serializers.URLField()},
    ),
    responses={
        200: inline_serializer(
            "VisionAutofillResponse",
            fields={
                "title": drf_serializers.CharField(),
                "category": drf_serializers.CharField(),
                "condition": drf_serializers.CharField(),
                "description": drf_serializers.CharField(),
            },
        )
    },
)
class VisionAutofillView(APIView):
    """
    POST /api/listings/vision-autofill
    Accepts a publicly accessible image URL (S3 CDN) and asks the vision model to return
    { title, category, condition, description } for a dorm-item listing.
    Called after the two-step S3 upload so the image is accessible before the
    listing is saved.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [VisionAutofillThrottle]

    _VALID_CATEGORIES = frozenset(Listing.Category.values)
    _VALID_CONDITIONS = frozenset(Listing.Condition.values)

    def post(self, request: Request) -> Response:
        from .revenue import campus_is_paid
        if not campus_is_paid(request) and not request.user.is_staff:
            return Response(
                {"detail": "Vision autofill requires a Standard or Premium campus license."},
                status=402,
            )
        image_url = str(request.data.get("image_url", "")).strip()
        if not image_url:
            return Response({"detail": "image_url is required."}, status=400)

        if not ai_available():
            return Response({"detail": "GEMINI_API_KEY is not configured."}, status=503)

        prompt = (
            "You are helping a college student post a dorm-item listing on a campus marketplace.\n\n"
            "Look at this image of a dorm item and return a JSON object with ONLY these four fields:\n"
            '- "title": short, specific product name (max 8 words, no brand names needed)\n'
            '- "category": one of ["storage","lighting","toiletries","comfort","supplies","decor","other"]\n'
            '- "condition": one of ["new","good","fair"] (new=like new/unused, good=used but clean, fair=worn/functional)\n'
            '- "description": 2-3 honest sentences describing the item and its condition\n\n'
            "Respond with ONLY the JSON object, no markdown, no extra text."
        )

        try:
            image_bytes, mime_type = fetch_image_bytes(image_url)
            parsed = generate_vision_json(image_bytes, mime_type, prompt, max_tokens=400)
        except ValueError as exc:
            return Response({"detail": f"Could not use that image: {exc}"}, status=400)
        except AiNotConfigured as exc:
            return Response({"detail": str(exc)}, status=503)
        except Exception:
            logger.exception("vision-autofill failed")
            return Response({"detail": "AI service unavailable."}, status=503)

        if not isinstance(parsed, dict):
            return Response({"detail": "Could not parse AI response."}, status=502)

        title = str(parsed.get("title", "")).strip()[:140] or "Dorm Item"
        category = parsed.get("category") if parsed.get("category") in self._VALID_CATEGORIES else "other"
        condition = parsed.get("condition") if parsed.get("condition") in self._VALID_CONDITIONS else "good"
        description = str(parsed.get("description", "")).strip()

        return Response(
            {
                "title": title,
                "category": category,
                "condition": condition,
                "description": description,
            }
        )


@extend_schema(
    operation_id="listings_check_duplicate",
    request=inline_serializer(
        "DuplicateCheckRequest",
        fields={
            "title": drf_serializers.CharField(),
            "category": drf_serializers.CharField(),
        },
    ),
    responses={
        200: inline_serializer(
            "DuplicateCheckResponse",
            fields={
                "is_duplicate": drf_serializers.BooleanField(),
                "duplicate_listing_id": drf_serializers.IntegerField(allow_null=True),
                "similarity_score": drf_serializers.FloatField(allow_null=True),
            },
        )
    },
)
class DuplicateCheckView(APIView):
    """
    POST /api/listings/check-duplicate
    Fast similarity check against the owner's last 30 active listings using
    lowercased title tokens + category.  Returns the most similar listing if
    cosine similarity > 0.75 so the frontend can warn before saving.
    """

    permission_classes = [permissions.IsAuthenticated]

    _THRESHOLD = 0.75

    @staticmethod
    def _tokenize(text: str) -> dict[str, int]:
        import re
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        freq: dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        return freq

    @staticmethod
    def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
        keys = set(a) | set(b)
        dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
        mag_a = sum(v * v for v in a.values()) ** 0.5
        mag_b = sum(v * v for v in b.values()) ** 0.5
        if not mag_a or not mag_b:
            return 0.0
        return dot / (mag_a * mag_b)

    def post(self, request: Request) -> Response:
        from .revenue import campus_is_paid
        if not campus_is_paid(request) and not request.user.is_staff:
            return Response(
                {"detail": "Duplicate detection requires a Standard or Premium campus license."},
                status=402,
            )
        title = str(request.data.get("title", "")).strip()
        category = str(request.data.get("category", "")).strip()

        if not title:
            return Response({"detail": "title is required."}, status=400)

        query_vec = self._tokenize(f"{title} {category}")

        recent = (
            Listing.objects.filter(
                owner=current_user(request),
                is_demo=False,
                status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
            )
            .only("id", "title", "category")
            .order_by("-created_at")[:30]
        )

        best_id = None
        best_score = 0.0
        for listing in recent:
            vec = self._tokenize(f"{listing.title} {listing.category}")
            score = self._cosine(query_vec, vec)
            if score > best_score:
                best_score = score
                best_id = listing.pk

        is_dup = best_score >= self._THRESHOLD
        return Response(
            {
                "is_duplicate": is_dup,
                "duplicate_listing_id": best_id if is_dup else None,
                "similarity_score": round(best_score, 3) if is_dup else None,
            }
        )


@extend_schema(
    operation_id="listings_rescue_suggestions",
    responses={
        200: inline_serializer(
            "RescueSuggestionsResponse",
            fields={
                "rescue_request_id": drf_serializers.IntegerField(allow_null=True),
                "rescue_title": drf_serializers.CharField(allow_null=True),
                "suggested_listings": ListingSerializer(many=True),
            },
        )
    },
)
class RescueSuggestionsView(APIView):
    """
    GET /api/listings/rescue-suggestions
    For authenticated users with an open RescueRequest, fires the AI matching
    task and returns the suggested listings immediately (or an empty list if no
    open request / no matches).
    """

    permission_classes = [permissions.IsAuthenticated]
    # A throttle that also counts GET: this read makes a model call, and the
    # write-only throttles used elsewhere would wave every request through.
    throttle_classes = [RescueSuggestionsThrottle]

    def get(self, request: Request) -> Response:
        from .revenue import campus_is_paid
        if not campus_is_paid(request) and not request.user.is_staff:
            return Response(
                {"detail": "Rescue suggestions require a Standard or Premium campus license."},
                status=402,
            )
        from ..models import RescueRequest
        from ..tasks import match_rescue_suggestions_task

        open_req = (
            RescueRequest.objects.filter(seeker=current_user(request), status=RescueRequest.Status.OPEN)
            .order_by("-created_at")
            .first()
        )
        if not open_req:
            return Response(
                {"rescue_request_id": None, "rescue_title": None, "suggested_listings": []}
            )

        # Run synchronously in the request (result is small); async for large scale
        matched_ids = match_rescue_suggestions_task(request.user.pk)

        if not matched_ids:
            return Response(
                {
                    "rescue_request_id": open_req.pk,
                    "rescue_title": open_req.title,
                    "suggested_listings": [],
                }
            )

        listings_qs = annotate_listing_queryset(
            Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS).filter(pk__in=matched_ids),
            current_user(request),
        )
        return Response(
            {
                "rescue_request_id": open_req.pk,
                "rescue_title": open_req.title,
                "suggested_listings": ListingSerializer(
                    listings_qs, many=True, context={"request": request}
                ).data,
            }
        )


# ---------------------------------------------------------------------------
# Public impact counter (no auth required)
# ---------------------------------------------------------------------------

_CATEGORY_CO2_LBS: dict[str, float] = {
    "storage": 3.5,
    "lighting": 2.0,
    "supplies": 1.0,
    "comfort": 4.0,
    "toiletries": 0.8,
    "decor": 1.5,
    "other": 2.0,
}


@extend_schema(
    operation_id="public_impact",
    responses={
        200: inline_serializer(
            "ImpactResponse",
            fields={
                "total_posted": drf_serializers.IntegerField(),
                "total_rescued": drf_serializers.IntegerField(),
                "total_retail_value": drf_serializers.DecimalField(max_digits=12, decimal_places=2),
                "estimated_lbs_diverted": drf_serializers.FloatField(),
                "rescue_rate_pct": drf_serializers.IntegerField(),
            },
        )
    },
)
class ImpactView(APIView):
    """
    Public campus-level impact aggregates, no authentication required.
    Used on the landing page and shareable in university sustainability reports.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, _request: Request) -> Response:
        rescued_statuses = [Listing.Status.PICKED_UP, Listing.Status.DONATED]

        total_posted = Listing.objects.filter(is_demo=False).count()
        rescued_qs = Listing.objects.filter(is_demo=False, status__in=rescued_statuses)
        total_rescued = rescued_qs.count()

        retail_agg = rescued_qs.aggregate(total=Sum("estimated_retail_value"))
        total_retail = Decimal(retail_agg["total"] or 0)

        category_counts = rescued_qs.values("category").annotate(n=Count("id"))
        estimated_lbs = sum(
            row["n"] * _CATEGORY_CO2_LBS.get(row["category"], 2.0)
            for row in category_counts
        )

        rescue_rate = round(total_rescued / total_posted * 100) if total_posted else 0

        return Response(
            {
                "total_posted": total_posted,
                "total_rescued": total_rescued,
                "total_retail_value": total_retail,
                "estimated_lbs_diverted": round(estimated_lbs, 1),
                "rescue_rate_pct": rescue_rate,
            }
        )
