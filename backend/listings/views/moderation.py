"""
Staff-only moderation endpoints.

POST /api/admin/listings/:id/moderate
  { "action": "approve" | "reject", "reason": "..." }

  - approve: sets moderation_status=approved, notifies owner
  - reject:  sets moderation_status=flagged (hidden from browse), notifies owner
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Listing, Notification


@extend_schema(
    operation_id="admin_moderate_listing",
    request=inline_serializer(
        "ModerationActionRequest",
        fields={
            "action": drf_serializers.ChoiceField(choices=["approve", "reject"]),
            "reason": drf_serializers.CharField(required=False, allow_blank=True),
        },
    ),
    responses={
        200: inline_serializer(
            "ModerationActionResponse",
            fields={
                "listing_id": drf_serializers.IntegerField(),
                "moderation_status": drf_serializers.CharField(),
            },
        )
    },
)
class ModerationActionView(APIView):
    """
    POST /api/admin/listings/:id/moderate
    Staff only.  Approve or reject a flagged listing.
    """

    permission_classes = [permissions.IsAdminUser]

    def post(self, request: Request, pk: int) -> Response:
        listing = get_object_or_404(Listing.all_objects, pk=pk)
        action = request.data.get("action", "")
        reason = str(request.data.get("reason", "")).strip()

        if action not in {"approve", "reject"}:
            return Response(
                {"detail": "action must be 'approve' or 'reject'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if action == "approve":
            listing.moderation_status = Listing.ModerationStatus.APPROVED
            listing.moderation_flag_reason = ""
            notif_title = "Your listing has been approved"
            notif_body = f'"{listing.title}" passed the moderation review and is now visible in browse.'
        else:
            listing.moderation_status = Listing.ModerationStatus.FLAGGED
            listing.moderation_flag_reason = reason
            notif_title = "Your listing was flagged for review"
            notif_body = (
                f'"{listing.title}" was flagged and hidden from browse.'
                + (f" Reason: {reason}" if reason else "")
                + " Contact support if you believe this is an error."
            )

        listing.save(update_fields=["moderation_status", "moderation_flag_reason", "updated_at"])

        # Notify the owner in-app
        Notification.objects.get_or_create(
            dedupe_key=f"moderation:{listing.pk}:{listing.moderation_status}",
            defaults={
                "user": listing.owner,
                "type": Notification.Type.SYSTEM,
                "title": notif_title,
                "body": notif_body,
                "link_path": f"/listings/{listing.pk}",
                "priority": Notification.Priority.HIGH if action == "reject" else Notification.Priority.NORMAL,
            },
        )

        return Response(
            {
                "listing_id": listing.pk,
                "moderation_status": listing.moderation_status,
            }
        )


@extend_schema(
    operation_id="admin_moderation_queue",
    responses={
        200: inline_serializer(
            "ModerationQueueItem",
            fields={
                "id": drf_serializers.IntegerField(),
                "title": drf_serializers.CharField(),
                "owner_email": drf_serializers.CharField(),
                "moderation_status": drf_serializers.CharField(),
                "moderation_flag_reason": drf_serializers.CharField(),
                "created_at": drf_serializers.DateTimeField(),
            },
            many=True,
        )
    },
)
class ModerationQueueView(APIView):
    """
    GET /api/admin/moderation
    Returns all flagged listings for staff review.
    """

    permission_classes = [permissions.IsAdminUser]

    def get(self, _request: Request) -> Response:
        flagged = (
            Listing.all_objects.filter(moderation_status=Listing.ModerationStatus.FLAGGED)
            .select_related("owner")
            .order_by("-created_at")[:200]
        )
        data = [
            {
                "id": l.pk,
                "title": l.title,
                "owner_email": l.owner.email,
                "moderation_status": l.moderation_status,
                "moderation_flag_reason": l.moderation_flag_reason,
                "created_at": l.created_at,
            }
            for l in flagged
        ]
        return Response(data)
