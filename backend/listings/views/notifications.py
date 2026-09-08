from __future__ import annotations

from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers as drf_serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Notification, NotificationPreference
from ..notifications import sync_user_notifications
from ..serializers import NotificationSerializer
from dormcycle.typed import current_user


class NotificationPreferenceWriteSerializer(drf_serializers.Serializer):
    """Validates one incoming preference row.

    Unvalidated writes used to persist arbitrary channel strings (silently
    disabling delivery, since nothing matches them later) and to 500 on a
    malformed time value.
    """

    notification_type = drf_serializers.ChoiceField(
        choices=NotificationPreference.NotificationType.choices
    )
    channel = drf_serializers.ChoiceField(
        choices=NotificationPreference.Channel.choices, required=False
    )
    quiet_hours_start = drf_serializers.TimeField(required=False, allow_null=True)
    quiet_hours_end = drf_serializers.TimeField(required=False, allow_null=True)

    def validate(self, attrs: dict) -> dict:
        start, end = attrs.get("quiet_hours_start"), attrs.get("quiet_hours_end")
        if (start is None) != (end is None) and ("quiet_hours_start" in attrs or "quiet_hours_end" in attrs):
            raise drf_serializers.ValidationError(
                "Set both quiet_hours_start and quiet_hours_end, or neither."
            )
        return attrs

_NotificationListResponse = inline_serializer(
    "NotificationListResponse",
    fields={
        "results": NotificationSerializer(many=True),
        "unread_count": drf_serializers.IntegerField(),
    },
)


class NotificationListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(operation_id="notifications_list", responses={200: _NotificationListResponse})
    def get(self, request: Request) -> Response:
        sync_user_notifications(current_user(request))
        queryset = Notification.objects.filter(user=current_user(request))

        unread_only = request.query_params.get("unread") == "1"
        if unread_only:
            queryset = queryset.filter(is_read=False)

        unread_count = Notification.objects.filter(user=current_user(request), is_read=False).count()

        try:
            limit = int(request.query_params.get("limit", "40"))
        except ValueError:
            limit = 40
        limit = max(1, min(limit, 100))

        notifications = list(queryset[:limit])
        return Response(
            {
                "results": NotificationSerializer(notifications, many=True).data,
                "unread_count": unread_count,
            }
        )


class NotificationDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[Notification]:
        return Notification.objects.filter(user=current_user(self.request))


class NotificationReadAllView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="notifications_read_all",
        request=None,
        responses={200: inline_serializer("NotificationReadAllResponse", fields={"updated": drf_serializers.IntegerField()})},
    )
    def post(self, request: Request) -> Response:
        queryset = Notification.objects.filter(user=current_user(request), is_read=False)
        count = queryset.update(is_read=True, read_at=timezone.now())
        return Response({"updated": count})


class NotificationDigestView(APIView):
    """
    GET /api/notifications/digest
    Phase 26 — last 7 days of notifications grouped by date then type.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from datetime import timedelta
        from collections import defaultdict

        cutoff = timezone.now() - timedelta(days=7)
        qs = (
            Notification.objects.filter(user=current_user(request), created_at__gte=cutoff)
            .order_by("-created_at")
        )

        # Group by date string, then by type
        by_day: dict = defaultdict(lambda: defaultdict(list))
        for n in qs:
            day = n.created_at.date().isoformat()
            by_day[day][n.type].append(NotificationSerializer(n).data)

        result = [
            {
                "date": day,
                "groups": [
                    {"type": ntype, "count": len(items), "latest": items[0]}
                    for ntype, items in type_map.items()
                ],
            }
            for day, type_map in sorted(by_day.items(), reverse=True)
        ]
        return Response({"days": result})


class NotificationPreferenceView(APIView):
    """
    GET/PATCH /api/auth/notification-preferences
    Phase 26 — read or bulk-update the user's per-type notification preferences.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        prefs = list(
            NotificationPreference.objects.filter(user=current_user(request)).values(
                "notification_type", "channel", "quiet_hours_start", "quiet_hours_end"
            )
        )
        # Fill defaults for types not yet saved
        existing = {p["notification_type"] for p in prefs}
        for choice_value, _ in NotificationPreference.NotificationType.choices:
            if choice_value not in existing:
                prefs.append({
                    "notification_type": choice_value,
                    "channel": "both",
                    "quiet_hours_start": None,
                    "quiet_hours_end": None,
                })
        return Response({"preferences": prefs})

    def patch(self, request: Request) -> Response:
        items = request.data.get("preferences", [])
        if not isinstance(items, list):
            return Response({"detail": "preferences must be a list."}, status=400)

        if len(items) > 20:
            return Response({"detail": "Too many preferences in one request."}, status=400)

        serializer = NotificationPreferenceWriteSerializer(data=items, many=True)
        serializer.is_valid(raise_exception=True)

        for item in serializer.validated_data:
            ntype = item.pop("notification_type")
            NotificationPreference.objects.update_or_create(
                user=current_user(request),
                notification_type=ntype,
                defaults=item,
            )

        return Response({"updated": len(items)})
