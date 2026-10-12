"""Payment endpoints: create (idempotent), list, detail, manual review."""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings
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


logger = logging.getLogger("risk.payments")

REQUEST_ID_MAX_LENGTH = 64  # request_id columns on payments, events and audit rows

# Payment states the creating request can answer with as its final response.
# PENDING and APPROVED are still on their way to one of them.
_SETTLED = (
    TransactionStatus.COMPLETED,
    TransactionStatus.FAILED,
    TransactionStatus.REVIEW,
    TransactionStatus.BLOCKED,
)


def _request_id(request: Request) -> str:
    # The middleware already bounds the header; this is the last stop before
    # a 64-character column, so it does not rely on that.
    return str(getattr(request, "request_id", "") or "")[:REQUEST_ID_MAX_LENGTH]


def _client_ip(request: Request) -> str:
    """The client address, trusting X-Forwarded-For exactly as far as the
    deployment says to.

    Same rule as DRF's throttles (REST_FRAMEWORK["NUM_PROXIES"]): with N
    trusted proxies the client is the Nth address from the right; with 0, or
    with the setting absent, the header is whatever the caller typed and
    only the socket address counts. Otherwise anyone could choose the
    network prefix the risk engine sees.
    """
    remote_addr = str(request.META.get("REMOTE_ADDR", "") or "")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    num_proxies = api_settings.NUM_PROXIES
    if not num_proxies or not forwarded:
        return remote_addr
    hops = forwarded.split(",")
    return hops[-min(int(num_proxies), len(hops))].strip()


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

        # Validate before touching the key: the key is compared on the
        # normalised request, so two spellings of one payment are one request.
        serializer = PaymentCreateSerializer(data=request.data)
        valid = serializer.is_valid()

        record = None
        if key:
            fingerprint = idempotency.payment_request_fingerprint(
                validated=serializer.validated_data if valid else None, raw=request.data
            )
            try:
                acquired = idempotency.acquire(
                    user=user, scope=idempotency.SCOPE_PAYMENTS, key=key, payload=fingerprint, legacy_payload=request.data
                )
            except idempotency.KeyInProgress as exc:
                return self._in_progress(exc)
            except idempotency.IdempotencyError as exc:
                return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
            if isinstance(acquired, idempotency.Replay):
                return self._replayed(acquired.body, acquired.status_code)
            if isinstance(acquired, idempotency.Recovered):
                return self._recovered(acquired, request)
            record = acquired

        if not valid:
            body: Any = serializer.errors
            if record is not None:
                idempotency.complete(record, status_code=status.HTTP_400_BAD_REQUEST, body=body)
            return Response(body, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
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
                idempotency_key=record,
            )
        except idempotency.OwnershipLost as exc:
            # The lease ran out and a retry took the key over; our payment
            # was rolled back by the failed bind. The retry is the request
            # of record now.
            return self._in_progress(exc)
        except Exception:
            if record is not None:
                # A no-op once the payment is committed (the key is bound in
                # the same transaction); before that it frees the key so the
                # retry is a real attempt.
                idempotency.release(record)
            raise

        body = self._body(result.transaction, request)
        if record is not None:
            try:
                idempotency.complete(record, status_code=status.HTTP_201_CREATED, body=body)
            except Exception:  # noqa: BLE001 - the payment succeeded; say so
                # The key stays bound, so a retry is answered from the
                # payment itself. Failing this request would report a
                # successful payment as an error.
                logger.exception("could not store the idempotent response for %s", result.transaction.public_id)
        return Response(body, status=status.HTTP_201_CREATED)

    @staticmethod
    def _in_progress(exc: idempotency.IdempotencyError) -> Response:
        response = Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        response["Retry-After"] = "1"
        return response

    @staticmethod
    def _replayed(body: Any, status_code: int) -> Response:
        response = Response(body, status=status_code)
        response["Idempotent-Replayed"] = "true"
        return response

    def _recovered(self, recovered: idempotency.Recovered, request: Request) -> Response:
        """The payment exists but its response was never stored: answer with
        the payment as it is now.

        While it is still being captured (by the original request, or later
        by the reconciler) nothing is stored, so the next replay renders it
        again; a stored body at this point would be a provisional answer
        served for the rest of the key's life. Once the state is one the
        original request would have returned, it is stored like any other
        final response.
        """
        body = self._body(recovered.transaction, request)
        if body["status"] in _SETTLED:
            idempotency.settle(recovered, status_code=status.HTTP_201_CREATED, body=body)
        return self._replayed(body, status.HTTP_201_CREATED)

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
