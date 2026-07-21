from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions

from ..models import Notification, Reservation, ReservationMessage
from ..permissions import IsReservationParticipant
from ..serializers import HandoffFeedbackSerializer, ReservationMessageSerializer, ReservationSerializer
from ..throttles import ReservationCreateThrottle
from dormcycle.typed import current_user


class ReservationListCreateView(generics.ListCreateAPIView):
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ReservationCreateThrottle]

    def get_queryset(self) -> QuerySet[Reservation]:
        user = current_user(self.request)
        return (
            Reservation.objects.select_related(
                "listing",
                "listing__owner",
                "listing__source_scan_session",
                "claimant",
            )
            .prefetch_related("feedback_entries__reviewer", "feedback_entries__reviewee")
            .filter(Q(claimant=user) | Q(listing__owner=user))
            .exclude(listing__is_demo=True)
            .distinct()
        )

    def perform_create(self, serializer: Any) -> None:
        serializer.save(claimant=self.request.user)


class ReservationDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = ReservationSerializer
    permission_classes = [permissions.IsAuthenticated, IsReservationParticipant]

    def get_queryset(self) -> QuerySet[Reservation]:
        user = current_user(self.request)
        return Reservation.objects.select_related(
            "listing",
            "listing__owner",
            "listing__source_scan_session",
            "claimant",
        ).prefetch_related(
            "feedback_entries__reviewer",
            "feedback_entries__reviewee",
        ).filter(Q(claimant=user) | Q(listing__owner=user))

    def perform_update(self, serializer: Any) -> None:
        reservation = serializer.save()
        from .dashboard import invalidate_user_dashboard_cache
        invalidate_user_dashboard_cache(reservation.claimant_id)
        invalidate_user_dashboard_cache(reservation.listing.owner_id)
        if reservation.status == "confirmed":
            try:
                from .partner import dispatch_webhook_event
                dispatch_webhook_event(
                    "reservation_confirmed",
                    {"id": reservation.pk, "listing_id": reservation.listing_id, "status": reservation.status},
                    reservation.listing.campus_name or "",
                )
            except Exception:
                pass


class ReservationFeedbackListCreateView(generics.ListCreateAPIView):
    serializer_class = HandoffFeedbackSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_reservation(self) -> Reservation:
        queryset = Reservation.objects.select_related(
            "listing",
            "listing__owner",
            "claimant",
        )
        if not self.request.user.is_staff:
            queryset = queryset.filter(
                Q(claimant=self.request.user) | Q(listing__owner=self.request.user)
            )
        return get_object_or_404(queryset, pk=self.kwargs["pk"])

    def get_queryset(self) -> QuerySet[Any]:
        reservation = self.get_reservation()
        return reservation.feedback_entries.select_related(
            "reviewer",
            "reviewee",
            "reservation__listing",
        )

    def get_serializer_context(self) -> dict[str, Any]:
        context = super().get_serializer_context()
        context["reservation"] = self.get_reservation()
        return context

    def perform_create(self, serializer: Any) -> None:
        reservation = self.get_reservation()
        feedback = serializer.save()
        # If the claimant says the item was AS_DESCRIBED, verify the listing condition
        from ..models import HandoffFeedback
        if (
            feedback.reviewer_id == reservation.claimant_id
            and HandoffFeedback.Tag.AS_DESCRIBED in (feedback.tags or [])
        ):
            reservation.listing.__class__.objects.filter(pk=reservation.listing_id).update(
                condition_verified=True
            )


class ReservationMessageListCreateView(generics.ListCreateAPIView):
    serializer_class = ReservationMessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _get_reservation(self) -> Reservation:
        queryset = Reservation.objects.filter(
            Q(claimant=self.request.user) | Q(listing__owner=self.request.user)
        )
        return get_object_or_404(queryset, pk=self.kwargs["pk"])

    def get_queryset(self) -> QuerySet[ReservationMessage]:
        reservation = self._get_reservation()
        return reservation.messages.select_related("sender")

    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        # Opening the thread is reading it: clear the counterparty's unread
        # marks so reservation-card badges stay honest.
        reservation = self._get_reservation()
        reservation.messages.exclude(sender=request.user).filter(read_at__isnull=True).update(
            read_at=timezone.now()
        )
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer: Any) -> None:
        reservation = self._get_reservation()
        msg = serializer.save(sender=self.request.user, reservation=reservation)
        # Notify the other party
        other = (
            reservation.listing.owner
            if self.request.user == reservation.claimant
            else reservation.claimant
        )
        sender_name = current_user(self.request).get_full_name() or current_user(self.request).email.split("@")[0]
        Notification.objects.get_or_create(
            dedupe_key=f"chat:{msg.pk}",
            defaults=dict(
                user=other,
                type=Notification.Type.CHAT,
                title=f"New message from {sender_name}",
                body=msg.body[:120],
                link_path=f"/handoffs/{reservation.pk}",
                priority=Notification.Priority.NORMAL,
            ),
        )
