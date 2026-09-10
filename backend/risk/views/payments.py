"""Payment endpoints: create (idempotent), list, detail, manual review."""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from dormcycle.pagination import StandardPagination
from dormcycle.typed import current_user

from ..engine.signals import coarse_ip_prefix
from ..models import Decision, PaymentTransaction, TransactionStatus
from ..permissions import IsStaff
from ..serializers import (
    ManualReviewSerializer,
    PaymentCreateSerializer,
    PaymentDetailSerializer,
    PaymentSummarySerializer,
)
from ..services import idempotency
from ..services.payments import PaymentInput, apply_manual_review, create_payment
from ..throttles import PaymentCreateThrottle


def _request_id(request: Request) -> str:
    return str(getattr(request, "request_id", "") or "")


def _client_ip(request: Request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return str(request.META.get("REMOTE_ADDR", ""))


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in ("1", "true", "yes")


def visible_payments(request: Request) -> "QuerySet[PaymentTransaction]":
    qs = PaymentTransaction.objects.for_user(current_user(request)).prefetch_related("evaluations__factors")
    params = request.query_params
    if not _truthy(params.get("include_simulation")):
        qs = qs.filter(is_simulation=False)
    if params.get("simulation"):
        qs = qs.filter(simulation_run__public_id=params["simulation"])
    if params.get("decision") in Decision.values:
        qs = qs.filter(decision=params["decision"])
    if params.get("status") in TransactionStatus.values:
        qs = qs.filter(status=params["status"])
    if params.get("band") in ("low", "medium", "high"):
        band = params["band"]
        if band == "high":
            qs = qs.filter(risk_score__gte=60)
        elif band == "medium":
            qs = qs.filter(risk_score__gte=30, risk_score__lt=60)
        else:
            qs = qs.filter(risk_score__lt=30)
    try:
        since = parse_datetime(params.get("since", "") or "")
    except ValueError:  # well-formed but impossible date
        since = None
    if since is not None:
        qs = qs.filter(created_at__gt=since)
    q = (params.get("q") or "").strip()[:80]
    if q:
        qs = qs.filter(Q(public_id__icontains=q) | Q(merchant_id__icontains=q) | Q(device_id__icontains=q) | Q(request_id__icontains=q))
    return qs


class PaymentListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PaymentCreateThrottle]

    def get(self, request: Request) -> Response:
        paginator = StandardPagination()
        page = paginator.paginate_queryset(visible_payments(request), request, view=self)
        return paginator.get_paginated_response(PaymentSummarySerializer(page, many=True, context={"request": request}).data)

    def post(self, request: Request) -> Response:
        user = current_user(request)
        try:
            key = idempotency.validate_key(request.headers.get("Idempotency-Key"))
        except idempotency.IdempotencyError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)

        record = None
        if key:
            try:
                acquired = idempotency.acquire(user=user, scope=idempotency.SCOPE_PAYMENTS, key=key, payload=request.data)
            except idempotency.KeyInProgress as exc:
                response = Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
                response["Retry-After"] = "1"
                return response
            except idempotency.IdempotencyError as exc:
                return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
            if isinstance(acquired, idempotency.Replay):
                response = Response(acquired.body, status=acquired.status_code)
                response["Idempotent-Replayed"] = "true"
                return response
            record = acquired

        serializer = PaymentCreateSerializer(data=request.data)
        if not serializer.is_valid():
            body: Any = serializer.errors
            if record is not None:
                idempotency.complete(record, status_code=status.HTTP_400_BAD_REQUEST, body=body)
            return Response(body, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        committed: list[PaymentTransaction] = []

        def on_committed(txn: PaymentTransaction) -> None:
            # The payment row and its evaluation are durable from here on. Bind
            # the key to it immediately so a crash later in the request (the
            # processor call, serialisation) can never let a retry create a
            # second payment: the retry replays this provisional response.
            committed.append(txn)
            if record is not None:
                idempotency.complete(record, status_code=status.HTTP_201_CREATED, body=self._body(txn, request), transaction_obj=txn)

        try:
            result = create_payment(
                user=user,
                data=PaymentInput(
                    merchant_id=data["merchant_id"],
                    amount=data["amount"],
                    currency=data["currency"],
                    payment_method=data["payment_method"],
                    device_id=data["device_id"],
                    merchant_category=data["merchant_category"],
                    country=data["country"],
                    sandbox_behavior=data["sandbox_behavior"],
                    metadata=data["metadata"],
                ),
                request_id=_request_id(request),
                ip_prefix=coarse_ip_prefix(_client_ip(request)),
                on_committed=on_committed,
            )
            body = self._body(result.transaction, request)
            if record is not None:
                idempotency.complete(record, status_code=status.HTTP_201_CREATED, body=body, transaction_obj=result.transaction)
        except Exception:
            if record is not None and not committed:
                # Nothing was persisted: forget the key so the retry is a real attempt.
                idempotency.release(record)
            raise
        return Response(body, status=status.HTTP_201_CREATED)

    @staticmethod
    def _body(txn: PaymentTransaction, request: Request) -> Any:
        fresh = PaymentTransaction.objects.prefetch_related("evaluations__factors").get(pk=txn.pk)
        return PaymentDetailSerializer(fresh, context={"request": request}).data


class PaymentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, public_id: str) -> Response:
        txn = get_object_or_404(
            PaymentTransaction.objects.for_user(current_user(request)).prefetch_related("evaluations__factors"),
            public_id=public_id,
        )
        return Response(PaymentDetailSerializer(txn, context={"request": request}).data)


class PaymentReviewView(APIView):
    """Staff resolves a payment that the engine held for review."""

    permission_classes = [IsStaff]

    def post(self, request: Request, public_id: str) -> Response:
        # Fraud Lab rows are labelled data, not payments: they are never reviewed or captured.
        txn = get_object_or_404(PaymentTransaction.objects.filter(is_simulation=False), public_id=public_id)
        serializer = ManualReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            apply_manual_review(
                txn=txn,
                reviewer=current_user(request),
                approve=serializer.validated_data["approve"],
                note=serializer.validated_data["note"],
                request_id=_request_id(request),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        txn = PaymentTransaction.objects.prefetch_related("evaluations__factors").get(pk=txn.pk)
        return Response(PaymentDetailSerializer(txn, context={"request": request}).data)
