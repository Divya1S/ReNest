from __future__ import annotations

import logging
import os

from django.shortcuts import get_object_or_404
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from dormcycle.typed import current_user
from typing import Any

logger = logging.getLogger(__name__)

# --- Tier pricing (Stripe price IDs set via env) ---
_TIER_PRICE_IDS = {
    "standard": os.getenv("STRIPE_PRICE_STANDARD", ""),
    "premium": os.getenv("STRIPE_PRICE_PREMIUM", ""),
}

_TIER_AMOUNTS = {
    "standard": 19900,  # $199.00
    "premium": 39900,   # $399.00
}


def _stripe() -> Any:
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    return stripe


def campus_is_paid(request: Any) -> bool:
    """Return True if the request user's campus has an active paid subscription."""
    if not request.user.is_authenticated:
        return False
    campus = getattr(request.user, "campus", None)
    if campus is None:
        return False
    return campus.is_paid


class CampusSubscribeView(APIView):
    """
    POST /api/campus/subscribe  { "tier": "standard"|"premium" }
    Creates a Stripe Checkout Session and returns the redirect URL.
    Staff/campus_manager only.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="campus_subscribe",
        request=inline_serializer(
            "CampusSubscribeRequest",
            fields={"tier": drf_serializers.ChoiceField(choices=["standard", "premium"])},
        ),
        responses={200: inline_serializer(
            "CampusSubscribeResponse",
            fields={"checkout_url": drf_serializers.URLField()},
        )},
    )
    def post(self, request: Request) -> Response:
        if not (request.user.is_staff or getattr(request.user, "is_campus_manager", False)):
            return Response({"detail": "Campus manager or staff access required."}, status=403)

        campus = getattr(request.user, "campus", None)
        if campus is None:
            return Response({"detail": "Your account is not associated with a campus."}, status=400)

        tier = request.data.get("tier")
        if tier not in _TIER_PRICE_IDS:
            return Response({"detail": "tier must be 'standard' or 'premium'."}, status=400)

        price_id = _TIER_PRICE_IDS[tier]
        secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        if not secret_key or not price_id:
            return Response(
                {"detail": "Stripe is not configured on this server. Contact support to subscribe."},
                status=503,
            )

        try:
            stripe = _stripe()
            from django.conf import settings as django_settings
            base_url = getattr(django_settings, "APP_BASE_URL", "https://renest.app")

            # Create or reuse Stripe customer
            if not campus.stripe_customer_id:
                customer = stripe.Customer.create(
                    email=campus.billing_email or current_user(request).email,
                    name=campus.name,
                    metadata={"campus_id": campus.pk, "campus_slug": campus.slug},
                )
                from accounts.models import Campus
                Campus.objects.filter(pk=campus.pk).update(stripe_customer_id=customer.id)
                campus.stripe_customer_id = customer.id

            session = stripe.checkout.Session.create(
                customer=campus.stripe_customer_id,
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="payment",
                success_url=f"{base_url}/campus-admin?subscribed=1",
                cancel_url=f"{base_url}/campus-admin?cancelled=1",
                metadata={"campus_id": str(campus.pk), "tier": tier},
            )
            return Response({"checkout_url": session.url})
        except Exception as exc:
            logger.exception("Stripe Checkout Session creation failed for campus %s", campus.pk)
            return Response({"detail": str(exc)}, status=502)


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    """
    POST /api/stripe/webhook
    Receives Stripe events. Verifies signature, updates Campus tier on successful payment.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request: Request) -> Response:
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        payload = request.body
        sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

        if webhook_secret:
            try:
                stripe = _stripe()
                event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
            except Exception as exc:
                logger.warning("Stripe webhook signature verification failed: %s", exc)
                return Response({"detail": "Invalid signature."}, status=400)
        elif settings.DEBUG:
            # Local development against `stripe trigger` without a secret.
            import json
            try:
                event = json.loads(payload)
            except Exception:
                return Response({"detail": "Invalid JSON."}, status=400)
        else:
            # Unsigned events are forgeable: anyone could POST a
            # checkout.session.completed and upgrade any campus for free.
            logger.error("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured.")
            return Response({"detail": "Webhook is not configured."}, status=503)

        if event.get("type") == "checkout.session.completed":
            self._handle_checkout_completed(event["data"]["object"])

        return Response({"received": True})

    def _handle_checkout_completed(self, session: dict) -> None:
        meta = session.get("metadata", {})
        campus_id = meta.get("campus_id")
        tier = meta.get("tier")
        if not campus_id or not tier:
            return

        from accounts.models import Campus, CampusSubscription
        from datetime import timedelta

        try:
            campus = Campus.objects.get(pk=int(campus_id))
        except Campus.DoesNotExist:
            return

        session_id = session.get("id", "")
        if session_id and CampusSubscription.objects.filter(stripe_session_id=session_id).exists():
            # Stripe retries delivery until it sees a 2xx; applying the same
            # session twice would extend the licence twice.
            logger.info("Ignoring duplicate Stripe session %s", session_id)
            return

        now = timezone.now()
        # License valid for ~120 days (one semester)
        expires = now + timedelta(days=120)
        try:
            with transaction.atomic():
                CampusSubscription.objects.create(
                    campus=campus,
                    stripe_session_id=session_id,
                    tier=tier,
                    amount_cents=session.get("amount_total", _TIER_AMOUNTS.get(tier, 0)),
                )
                Campus.objects.filter(pk=campus.pk).update(
                    subscription_tier=tier,
                    license_expires_at=expires,
                )
        except IntegrityError:
            # Concurrent redelivery lost the race on the unique session id.
            logger.info("Concurrent duplicate Stripe session %s ignored", session_id)
            return
        logger.info("Campus %s upgraded to %s tier, expires %s", campus.slug, tier, expires.date())


class ListingBoostView(APIView):
    """
    POST /api/listings/:id/boost  { "amount_cents": 100|200|300|400|500 }
    Owner creates a Stripe Payment Intent for a $1–$5 boost.
    Returns { client_secret, boost_hours } — frontend completes payment via Stripe.js.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="listing_boost",
        request=inline_serializer(
            "ListingBoostRequest",
            fields={"amount_cents": drf_serializers.IntegerField(min_value=100, max_value=500)},
        ),
        responses={200: inline_serializer(
            "ListingBoostResponse",
            fields={
                "client_secret": drf_serializers.CharField(),
                "boost_hours": drf_serializers.IntegerField(),
            },
        )},
    )
    def post(self, request: Request, pk: int) -> Response:
        from ..models import Listing
        listing = get_object_or_404(Listing, pk=pk, owner=request.user)

        amount_cents = int(request.data.get("amount_cents", 0))
        if amount_cents not in (100, 200, 300, 400, 500):
            return Response({"detail": "amount_cents must be 100, 200, 300, 400, or 500."}, status=400)

        secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        if not secret_key:
            return Response(
                {"detail": "Stripe is not configured on this server."},
                status=503,
            )

        try:
            stripe = _stripe()
            intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency="usd",
                automatic_payment_methods={"enabled": True},
                metadata={
                    "listing_id": str(listing.pk),
                    "owner_id": str(request.user.pk),
                    "type": "listing_boost",
                },
            )
            # Each $1 = 24h boost
            boost_hours = (amount_cents // 100) * 24
            return Response({"client_secret": intent.client_secret, "boost_hours": boost_hours})
        except Exception as exc:
            logger.exception("Stripe PaymentIntent creation failed for listing %s", pk)
            return Response({"detail": str(exc)}, status=502)


class ListingBoostConfirmView(APIView):
    """
    POST /api/listings/:id/boost-confirm  { "payment_intent_id": "pi_..." }
    Called by frontend after Stripe.js confirms the payment.
    Sets boosted_until on the listing.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        from ..models import Listing, ListingBoostPayment
        from datetime import timedelta

        listing = get_object_or_404(Listing, pk=pk, owner=request.user)
        pi_id = str(request.data.get("payment_intent_id", "")).strip()

        secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        if not secret_key:
            return Response({"detail": "Stripe is not configured."}, status=503)

        if not pi_id:
            return Response({"detail": "payment_intent_id is required."}, status=400)

        try:
            stripe = _stripe()
            intent = stripe.PaymentIntent.retrieve(pi_id)
        except Exception:
            logger.exception("Could not retrieve payment intent %s", pi_id)
            return Response({"detail": "Payment could not be verified. Try again shortly."}, status=502)

        if intent.status != "succeeded":
            return Response({"detail": "Payment not confirmed yet."}, status=400)

        meta = intent.metadata
        if str(meta.get("listing_id")) != str(pk) or meta.get("type") != "listing_boost":
            return Response({"detail": "Payment intent does not match this listing."}, status=400)

        boost_hours = (intent.amount // 100) * 24
        now = timezone.now()

        # A payment may be redeemed exactly once. Without this record the same
        # succeeded intent could be POSTed repeatedly, each call stacking
        # another day of boosted placement onto a single $1 payment.
        try:
            with transaction.atomic():
                ListingBoostPayment.objects.create(
                    listing=listing,
                    payment_intent_id=pi_id,
                    amount_cents=intent.amount,
                )
                current_boost = listing.boosted_until
                base = current_boost if (current_boost and current_boost > now) else now
                new_boosted_until = base + timedelta(hours=boost_hours)
                Listing.objects.filter(pk=pk).update(boosted_until=new_boosted_until)
        except IntegrityError:
            return Response({"detail": "This payment has already been redeemed."}, status=409)

        return Response({"boosted_until": new_boosted_until.isoformat(), "boost_hours": boost_hours})


class OrganizationListView(APIView):
    """GET /api/organizations — returns verified organizations for the tip jar selector."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from ..models import Organization
        from ..serializers import OrganizationSerializer
        orgs = Organization.objects.filter(verified=True)
        campus_name = request.query_params.get("campus")
        if campus_name:
            orgs = orgs.filter(campus_name__icontains=campus_name)
        return Response(OrganizationSerializer(orgs, many=True).data)


class DonationIntentCreateView(APIView):
    """POST /api/listings/:id/donate-intent — log a tip allocation to an organization."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        from ..models import DonationIntent, Listing, Organization
        listing = get_object_or_404(Listing, pk=pk, owner=request.user)
        org_id = request.data.get("organization_id")
        percentage = int(request.data.get("percentage", 10))
        if not (1 <= percentage <= 100):
            return Response({"detail": "percentage must be 1–100."}, status=400)
        org = get_object_or_404(Organization, pk=org_id, verified=True)
        di, _ = DonationIntent.objects.get_or_create(
            listing=listing,
            organization=org,
            owner=current_user(request),
            defaults={"percentage": percentage},
        )
        return Response({"id": di.pk, "organization": org.name, "percentage": di.percentage})
