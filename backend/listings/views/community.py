from __future__ import annotations

from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Announcement, Reservation, ReservationMessage
from dormcycle.typed import current_user
from typing import Any


class LeaderboardView(APIView):
    """GET /api/leaderboard — top 20 rescuers on the user's campus (opt-in)."""

    def get(self, request: Request) -> Response:
        campus = getattr(request.user, "campus", None)
        if not campus:
            return Response({"detail": "No campus associated with your account."}, status=400)

        from django.contrib.auth import get_user_model
        User = get_user_model()

        semester = request.query_params.get("period", "all")
        qs = Reservation.objects.filter(
            listing__owner__campus=campus,
            listing__owner__show_on_leaderboard=True,
            status=Reservation.Status.COMPLETED,
        )
        if semester == "semester":
            from datetime import timedelta
            qs = qs.filter(updated_at__gte=timezone.now() - timedelta(days=120))

        rows = (
            qs.values("listing__owner_id")
            .annotate(
                item_count=Count("id"),
                total_value=Sum("listing__estimated_retail_value"),
            )
            .order_by("-item_count")[:20]
        )

        user_ids = [r["listing__owner_id"] for r in rows]  # type: ignore[index]  # .values() rows are dicts
        users = {u.id: u for u in User.objects.filter(pk__in=user_ids)}

        board = []
        for rank, row in enumerate(rows, start=1):
            user = users.get(row["listing__owner_id"])  # type: ignore[index]  # .values() rows are dicts
            if not user:
                continue
            board.append({
                "rank": rank,
                "display_name": user.display_name or "Anonymous",
                "milestone": user.milestone,
                "completion_rate": user.completion_rate,
                "item_count": row["item_count"],  # type: ignore[index]  # .values() rows are dicts
                "total_value": float(row["total_value"] or 0),  # type: ignore[index]  # .values() rows are dicts
                "is_me": user.id == request.user.id,
            })

        # Include requesting user's rank even if outside top 20
        my_rank = None
        if not any(e["is_me"] for e in board):
            my_row = (
                qs.filter(listing__owner=current_user(request))
                .aggregate(item_count=Count("id"), total_value=Sum("listing__estimated_retail_value"))
            )
            if my_row["item_count"]:
                my_rank = (
                    qs.values("listing__owner_id")
                    .annotate(n=Count("id"))
                    .filter(n__gt=my_row["item_count"])
                    .count()
                ) + 1

        return Response({"leaderboard": board, "my_rank": my_rank})


class BuildingLeaderboardView(APIView):
    """GET /api/leaderboard/buildings — dorm vs dorm aggregate for user's campus."""

    def get(self, request: Request) -> Response:
        campus = getattr(request.user, "campus", None)
        if not campus:
            return Response({"detail": "No campus associated with your account."}, status=400)

        rows = (
            Reservation.objects.filter(
                listing__owner__campus=campus,
                listing__building__gt="",
                status=Reservation.Status.COMPLETED,
            )
            .values("listing__building")
            .annotate(
                item_count=Count("id"),
                total_value=Sum("listing__estimated_retail_value"),
                contributors=Count("listing__owner_id", distinct=True),
            )
            .order_by("-item_count")[:15]
        )

        board = [
            {
                "building": r["listing__building"],  # type: ignore[index]  # .values() rows are dicts
                "item_count": r["item_count"],  # type: ignore[index]  # .values() rows are dicts
                "total_value": float(r["total_value"] or 0),  # type: ignore[index]  # .values() rows are dicts
                "contributors": r["contributors"],  # type: ignore[index]  # .values() rows are dicts
            }
            for r in rows
        ]
        return Response({"buildings": board})


class AnnouncementSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.display_name", read_only=True, default="")

    class Meta:
        from ..models import Announcement as _A
        model = _A
        fields = ("id", "title", "body", "is_pinned", "expires_at", "author_name", "created_at")
        read_only_fields = ("id", "author_name", "created_at")


class AnnouncementListCreateView(APIView):
    """GET /api/announcements — active announcements for user's campus.
       POST — campus manager only.
    """

    def get(self, request: Request) -> Response:
        campus = getattr(request.user, "campus", None)
        if not campus:
            return Response([], status=200)

        now = timezone.now()
        qs = Announcement.objects.filter(campus=campus).filter(
            expires_at__isnull=True
        ) | Announcement.objects.filter(campus=campus, expires_at__gt=now)
        qs = qs.order_by("-is_pinned", "-created_at")

        return Response(AnnouncementSerializer(qs, many=True).data)

    def post(self, request: Request) -> Response:
        if not (current_user(request).is_campus_manager or current_user(request).is_staff):
            return Response({"detail": "Campus manager access required."}, status=status.HTTP_403_FORBIDDEN)

        campus = getattr(request.user, "campus", None)
        if not campus:
            return Response({"detail": "No campus on your account."}, status=400)

        serializer = AnnouncementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        announcement = serializer.save(campus=campus, author=request.user)
        return Response(AnnouncementSerializer(announcement).data, status=status.HTTP_201_CREATED)


class AnnouncementDetailView(APIView):
    """PATCH/DELETE /api/announcements/:id — campus manager only."""

    def _get_object(self, pk: int, user: Any) -> Announcement:
        announcement = Announcement.objects.get(pk=pk)
        campus = getattr(user, "campus", None)
        if announcement.campus != campus and not user.is_staff:
            raise PermissionError
        return announcement

    def patch(self, request: Request, pk: int) -> Response:
        if not (current_user(request).is_campus_manager or current_user(request).is_staff):
            return Response({"detail": "Campus manager access required."}, status=status.HTTP_403_FORBIDDEN)
        try:
            announcement = self._get_object(pk, request.user)
        except (Announcement.DoesNotExist, PermissionError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = AnnouncementSerializer(announcement, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AnnouncementSerializer(announcement).data)

    def delete(self, request: Request, pk: int) -> Response:
        if not (current_user(request).is_campus_manager or current_user(request).is_staff):
            return Response({"detail": "Campus manager access required."}, status=status.HTTP_403_FORBIDDEN)
        try:
            announcement = self._get_object(pk, request.user)
        except (Announcement.DoesNotExist, PermissionError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        announcement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReservationThankView(APIView):
    """POST /api/reservations/:id/thank — claimant sends a thank-you message."""

    def post(self, request: Request, pk: int) -> Response:
        try:
            reservation = Reservation.objects.select_related(
                "listing__owner", "claimant"
            ).get(pk=pk, claimant=current_user(request))
        except Reservation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if reservation.status != Reservation.Status.COMPLETED:
            return Response(
                {"detail": "Thank-you messages can only be sent after a completed handoff."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body = request.data.get("body", "").strip()
        if not body:
            item_title = reservation.listing.title
            body = f"Thanks so much for the {item_title}! Really appreciate it."

        msg = ReservationMessage.objects.create(
            reservation=reservation,
            sender=current_user(request),
            body=body,
        )

        # Notify owner
        from ..models import Notification
        Notification.objects.get_or_create(
            dedupe_key=f"thankyou:{reservation.pk}",
            defaults={
                "user": reservation.listing.owner,
                "title": f"{current_user(request).display_name} sent you a thank you",
                "body": body[:120],
                "link_path": f"/handoffs/{reservation.pk}",
                "priority": Notification.Priority.NORMAL,
            },
        )

        return Response({"id": msg.id, "body": msg.body}, status=status.HTTP_201_CREATED)
