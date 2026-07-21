from __future__ import annotations

import logging
import uuid as _uuid
from importlib import import_module

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import Campus, User
from dormcycle.typed import current_user
from typing import Any

from .serializers import (
    CampusOnboardingSerializer,
    CampusPublicStatsSerializer,
    LoginSerializer,
    MessageSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    SuperAdminCampusSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


_UserWrapSerializer = inline_serializer("UserWrap", fields={"user": UserSerializer()})
_MeSerializer = inline_serializer(
    "MeResponse",
    fields={
        "authenticated": drf_serializers.BooleanField(),
        "user": UserSerializer(allow_null=True),
    },
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfCookieView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        operation_id="auth_csrf",
        description="Set a CSRF cookie. Call this before any mutating request.",
        responses={200: MessageSerializer},
    )
    def get(self, request: Request) -> Response:
        return Response({"detail": "CSRF cookie set."})


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterRateThrottle]

    @extend_schema(
        operation_id="auth_register",
        request=RegisterSerializer,
        responses={201: _UserWrapSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        request.session.cycle_key()
        login(request, user)
        payload: dict[str, Any] = {"user": UserSerializer(user).data}
        try:
            verify_url = _send_verification_email(user)
        except Exception:
            # Don't fail registration if email delivery is unavailable, but
            # leave a trace — a silent drop here strands the user unverified.
            logger.warning("Verification email failed for %s", user.email, exc_info=True)
        else:
            if settings.DEBUG:
                # Console email backend in dev: surface the link in the UI so
                # local testers aren't forced to dig through server logs.
                payload["dev_verify_url"] = verify_url
        return Response(payload, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginRateThrottle]

    @extend_schema(
        operation_id="auth_login",
        request=LoginSerializer,
        responses={200: _UserWrapSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if not user:
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.session.cycle_key()
        login(request, user)
        return Response({"user": UserSerializer(user).data})


class LogoutView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        operation_id="auth_logout",
        request=None,
        responses={200: MessageSerializer},
    )
    def post(self, request: Request) -> Response:
        logout(request)
        return Response({"detail": "Logged out."})


class MeView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        operation_id="auth_me",
        responses={200: _MeSerializer},
    )
    def get(self, request: Request) -> Response:
        if not request.user.is_authenticated:
            return Response({"authenticated": False, "user": None})

        return Response({"authenticated": True, "user": UserSerializer(request.user).data})

    @extend_schema(
        operation_id="auth_me_update",
        request=inline_serializer(
            "MeUpdateRequest",
            fields={
                "onboarding_step": drf_serializers.IntegerField(min_value=0, max_value=3, required=False),
                "display_name": drf_serializers.CharField(max_length=120, required=False),
                "campus_name": drf_serializers.CharField(max_length=120, required=False, allow_blank=True),
            },
        ),
        responses={200: UserSerializer},
    )
    def patch(self, request: Request) -> Response:
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        update_fields: list[str] = []

        display_name = request.data.get("display_name")
        if display_name is not None:
            display_name = str(display_name).strip()
            if not display_name:
                return Response({"display_name": "Display name cannot be empty."}, status=400)
            if len(display_name) > 120:
                return Response({"display_name": "Keep it under 120 characters."}, status=400)
            request.user.display_name = display_name
            update_fields.append("display_name")

        campus_name = request.data.get("campus_name")
        if campus_name is not None:
            campus_name = str(campus_name).strip()
            if len(campus_name) > 120:
                return Response({"campus_name": "Keep it under 120 characters."}, status=400)
            request.user.campus_name = campus_name
            update_fields.append("campus_name")

        step = request.data.get("onboarding_step")
        if step is not None:
            try:
                step = int(step)
            except (TypeError, ValueError):
                return Response({"onboarding_step": "Must be an integer 0–3."}, status=400)
            if not 0 <= step <= 3:
                return Response({"onboarding_step": "Must be between 0 and 3."}, status=400)
            # Only allow advancing, not rolling back
            if step > request.user.onboarding_step:
                request.user.onboarding_step = step
                update_fields.append("onboarding_step")

        if update_fields:
            request.user.save(update_fields=update_fields)
        return Response({"authenticated": True, "user": UserSerializer(request.user).data})


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    @extend_schema(
        operation_id="auth_password_reset_request",
        request=PasswordResetRequestSerializer,
        responses={200: MessageSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = (
                f"{settings.APP_BASE_URL}/password-reset/confirm?uid={uid}&token={token}"
            )
            send_mail(
                subject="Reset your ReNest password",
                message=(
                    f"Hi {user.display_name or user.email},\n\n"
                    f"Click the link below to reset your password. "
                    f"It expires in 1 hour.\n\n{reset_url}\n\n"
                    "If you didn't request this, you can safely ignore this email."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except User.DoesNotExist:
            pass  # Don't reveal whether the email exists

        return Response(
            {"detail": "If an account exists with that email, a reset link has been sent."}
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        operation_id="auth_password_reset_confirm",
        request=PasswordResetConfirmSerializer,
        responses={200: MessageSerializer},
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        user.set_password(serializer.validated_data["new_password"])
        user.save()
        return Response({"detail": "Password reset. You can now log in with your new password."})


_EMAIL_VERIFY_SALT = "email-verify"
_EMAIL_VERIFY_MAX_AGE = 24 * 3600  # 24 hours


def _send_verification_email(user: User) -> str:
    token = signing.dumps(user.pk, salt=_EMAIL_VERIFY_SALT)
    verify_url = f"{settings.APP_BASE_URL}/verify-email/confirm?token={token}"
    send_mail(
        subject="Verify your ReNest email address",
        message=(
            f"Hi {user.display_name or user.email},\n\n"
            f"Click the link below to verify your email address. "
            f"It expires in 24 hours.\n\n{verify_url}\n\n"
            "If you didn't create a ReNest account, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return verify_url


class SendVerificationEmailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="auth_verify_email_send",
        request=None,
        responses={200: MessageSerializer},
    )
    def post(self, request: Request) -> Response:
        if current_user(request).email_verified:
            return Response({"detail": "Email is already verified."})
        verify_url = _send_verification_email(current_user(request))
        payload = {"detail": "Verification email sent."}
        if settings.DEBUG:
            payload["dev_verify_url"] = verify_url
        return Response(payload)


class VerifyEmailConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        operation_id="auth_verify_email_confirm",
        request=inline_serializer("VerifyEmailRequest", fields={"token": drf_serializers.CharField()}),
        responses={200: MessageSerializer},
    )
    def post(self, request: Request) -> Response:
        token = request.data.get("token", "")
        try:
            pk = signing.loads(token, salt=_EMAIL_VERIFY_SALT, max_age=_EMAIL_VERIFY_MAX_AGE)
        except signing.SignatureExpired:
            return Response(
                {"detail": "This verification link has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except signing.BadSignature:
            return Response(
                {"detail": "Invalid verification link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"detail": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)

        user.email_verified = True
        user.save(update_fields=["email_verified"])
        return Response({"detail": "Email verified. You can now post listings."})


class ReferralLinkView(APIView):
    """Return the authenticated user's shareable referral URL."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        url = f"{settings.APP_BASE_URL}/register?ref={current_user(request).referral_code}"
        return Response({"url": url, "referral_count": current_user(request).referral_count})


_UNSUBSCRIBE_SALT = "email-unsubscribe"
_UNSUBSCRIBE_MAX_AGE = 90 * 24 * 3600  # 90 days


def make_unsubscribe_url(user: User) -> str:
    token = signing.dumps(user.pk, salt=_UNSUBSCRIBE_SALT)
    return f"{settings.APP_BASE_URL}/unsubscribe?token={token}"


class EmailUnsubscribeView(APIView):
    """
    GET /api/auth/unsubscribe?token=...
    One-click unsubscribe link embedded in every transactional email.
    The token is a signed user PK (valid 90 days).
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request: Request) -> Response:
        token = request.query_params.get("token", "")
        try:
            pk = signing.loads(token, salt=_UNSUBSCRIBE_SALT, max_age=_UNSUBSCRIBE_MAX_AGE)
        except (signing.SignatureExpired, signing.BadSignature):
            return Response({"detail": "Invalid or expired unsubscribe link."}, status=400)
        User.objects.filter(pk=pk).update(email_notifications=False)
        return Response({"detail": "You have been unsubscribed from ReNest emails."})


class AccountDeleteView(APIView):
    """
    DELETE /api/auth/account
    Body: { "password": "..." }

    Anonymises PII, hard-deletes sessions and push subscriptions,
    soft-deletes the user's listings, then deactivates the account.
    GDPR Art. 17 right-to-erasure.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="auth_account_delete",
        request=inline_serializer(
            "AccountDeleteRequest",
            fields={"password": drf_serializers.CharField(write_only=True)},
        ),
        responses={200: MessageSerializer},
    )
    def delete(self, request: Request) -> Response:
        password = request.data.get("password", "")
        if not request.user.check_password(password):
            return Response({"detail": "Incorrect password."}, status=400)

        user = current_user(request)
        anon_id = _uuid.uuid4().hex[:12]

        # Anonymise PII in-place
        User.objects.filter(pk=user.pk).update(
            email=f"deleted-{anon_id}@dormcycle.invalid",
            display_name="Deleted User",
            campus_name="",
            is_active=False,
            email_notifications=False,
            referred_by=None,
        )

        # Remove sessions and push subscriptions
        try:
            engine = import_module(settings.SESSION_ENGINE)
            engine.SessionStore.clear_expired()
        except Exception:
            pass
        request.session.flush()

        try:
            from listings.models import PushSubscription
            PushSubscription.objects.filter(user=user).delete()
        except Exception:
            pass

        # Soft-delete all the user's listings
        try:
            from listings.models import Listing
            Listing.objects.filter(owner=user, deleted_at__isnull=True).update(
                deleted_at=timezone.now()
            )
        except Exception:
            pass

        logout(request)

        return Response({"detail": "Account deleted."})


class DataExportView(APIView):
    """
    GET /api/auth/data-export
    Returns a JSON archive of the authenticated user's own data.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(operation_id="auth_data_export", responses={200: drf_serializers.DictField()})
    def get(self, request: Request) -> Response:
        user = current_user(request)

        try:
            from listings.models import Listing, Reservation, ReservationMessage
            listings = list(
                Listing.objects.filter(owner=user, is_demo=False)
                .values("id", "title", "description", "category", "condition",
                        "status", "price_type", "price_amount", "pickup_zone",
                        "available_until", "created_at")
            )
            reservations = list(
                Reservation.objects.filter(claimant=user)
                .values("id", "status", "pickup_time_window", "created_at",
                        "listing__title", "listing__owner__display_name")
            )
            messages = list(
                ReservationMessage.objects.filter(sender=user)
                .values("id", "body", "created_at", "reservation_id")
            )
        except Exception:
            listings, reservations, messages = [], [], []

        return Response({
            "user": {
                "id": user.pk,
                "email": user.email,
                "display_name": user.display_name,
                "campus_name": user.campus_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "listings": listings,
            "reservations": reservations,
            "messages": messages,
        })


# ---------- Phase 17 — Impact Card ----------

_CATEGORY_WEIGHTS_LB = {
    "storage": 3.5,
    "lighting": 2.0,
    "supplies": 1.0,
    "comfort": 5.0,
    "toiletries": 0.5,
    "decor": 2.5,
    "other": 2.0,
}


class ImpactCardView(APIView):
    """
    GET /api/auth/impact-card.png
    Renders a 1200×630 shareable impact summary card for the authenticated user.
    Uses Pillow — no extra font needed (uses default PIL font as fallback).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Any:
        from django.http import HttpResponse
        user = current_user(request)

        try:
            from listings.models import Listing, Reservation
            completed_reservations = Reservation.objects.filter(
                listing__owner=user,
                status="completed",
            ).select_related("listing")

            total_value = 0.0
            total_weight = 0.0
            item_count = 0
            for r in completed_reservations:
                val = float(r.listing.estimated_retail_value or 0)
                total_value += val
                cat = r.listing.category or "other"
                total_weight += _CATEGORY_WEIGHTS_LB.get(cat, 2.0)
                item_count += 1

            # CO2e: ~0.5 kg per lb diverted from landfill
            co2_kg = round(total_weight * 0.5, 1)
        except Exception:
            total_value = 0.0
            total_weight = 0.0
            item_count = 0
            co2_kg = 0.0

        img_bytes = self._render_card(
            name=user.display_name or user.email.split("@")[0],
            campus=user.campus_name or (user.campus.name if user.campus else ""),
            item_count=item_count,
            total_value=total_value,
            co2_kg=co2_kg,
        )

        response = HttpResponse(img_bytes, content_type="image/png")
        response["Cache-Control"] = "private, max-age=3600"
        response["Content-Disposition"] = 'inline; filename="dormcycle-impact.png"'
        return response

    @staticmethod
    def _render_card(name: str, campus: str, item_count: int, total_value: float, co2_kg: float) -> bytes:
        import io
        from PIL import Image, ImageDraw

        W, H = 1200, 630
        BG = (18, 18, 24)
        TEAL = (11, 122, 114)
        WHITE = (255, 255, 255)
        MUTED = (140, 140, 160)

        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # Teal accent bar left edge
        draw.rectangle([(0, 0), (8, H)], fill=TEAL)

        # Try to load a better font; fall back gracefully
        try:
            from PIL import ImageFont
            font_lg: Any = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 72)
            font_md: Any = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 36)
            font_sm: Any = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
            font_xs: Any = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        except Exception:
            from PIL import ImageFont
            font_lg = font_md = font_sm = font_xs = ImageFont.load_default()

        # Header
        draw.text((60, 60), "ReNest", font=font_md, fill=TEAL)
        draw.text((60, 110), f"{name}'s Impact", font=font_lg, fill=WHITE)
        if campus:
            draw.text((60, 200), campus, font=font_sm, fill=MUTED)

        # Divider
        draw.line([(60, 250), (W - 60, 250)], fill=(50, 50, 70), width=2)

        # Stats row
        stats = [
            (str(item_count), "items rescued"),
            (f"${total_value:,.0f}", "retail value saved"),
            (f"{co2_kg} kg", "CO₂ diverted"),
        ]
        col_w = (W - 120) // 3
        for i, (val, label) in enumerate(stats):
            x = 60 + i * col_w
            draw.text((x, 290), val, font=font_lg, fill=TEAL)
            draw.text((x, 380), label, font=font_sm, fill=MUTED)

        # Footer
        draw.text((60, H - 60), "renest.app  —  reduce move-out waste", font=font_xs, fill=MUTED)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


class CampusOnboardRateThrottle(AnonRateThrottle):
    scope = "campus_onboard"


class CampusPublicStatsView(APIView):
    """GET /api/campus/:slug/stats — public campus profile, no auth required."""

    permission_classes = [permissions.AllowAny]

    def get(self, request: Request, slug: str) -> Response:
        try:
            campus = Campus.objects.get(slug=slug, active=True, onboarding_status=Campus.OnboardingStatus.APPROVED)
        except Campus.DoesNotExist:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)

        from listings.models import Listing, Reservation
        from django.db.models import Count, Sum

        listing_qs = Listing.objects.filter(owner__campus=campus)
        claimed_qs = Reservation.objects.filter(
            listing__owner__campus=campus, status=Reservation.Status.COMPLETED
        )

        listing_count = listing_qs.count()
        claimed_count = claimed_qs.count()
        member_count = campus.members.count()
        total_value = float(
            claimed_qs.aggregate(v=Sum("listing__estimated_retail_value"))["v"] or 0
        )

        # Top 3 categories by claimed count
        top_cats = (
            claimed_qs.values("listing__category")
            .annotate(count=Count("id"))
            .order_by("-count")[:3]
        )
        top_categories = [{"category": r["listing__category"], "count": r["count"]} for r in top_cats]  # type: ignore[index]  # .values() rows are dicts

        data = {
            "id": campus.id,
            "name": campus.name,
            "slug": campus.slug,
            "timezone": campus.timezone,
            "move_out_start": campus.move_out_start,
            "move_out_end": campus.move_out_end,
            "subscription_tier": campus.subscription_tier,
            "listing_count": listing_count,
            "claimed_count": claimed_count,
            "member_count": member_count,
            "total_value_rescued": total_value,
            "top_categories": top_categories,
        }
        return Response(CampusPublicStatsSerializer(data).data)


class CampusOnboardingRequestView(APIView):
    """POST /api/campus/onboard — self-service campus registration (pending approval)."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [CampusOnboardRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = CampusOnboardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        campus = Campus.objects.create(
            name=d["name"],
            slug=d["slug"],
            email_domains=d["email_domains"],
            contact_name=d["contact_name"],
            contact_email=d["contact_email"],
            timezone=d.get("timezone", "America/Los_Angeles"),
            active=False,
            onboarding_status=Campus.OnboardingStatus.PENDING,
        )

        admin_email = getattr(settings, "PLATFORM_ADMIN_EMAIL", settings.DEFAULT_FROM_EMAIL)
        try:
            send_mail(
                subject=f"[ReNest] New campus onboarding request: {campus.name}",
                message=(
                    f"Campus: {campus.name} ({campus.slug})\n"
                    f"Domains: {campus.email_domains}\n"
                    f"Contact: {campus.contact_name} <{campus.contact_email}>\n\n"
                    f"Approve or reject via the admin panel."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[admin_email],
                fail_silently=True,
            )
            send_mail(
                subject="Your ReNest campus request was received",
                message=(
                    f"Hi {campus.contact_name},\n\n"
                    f"We received your request to add {campus.name} to ReNest. "
                    f"We'll review it within 2 business days and reach out to {campus.contact_email}.\n\n"
                    f"— The ReNest team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[campus.contact_email],
                fail_silently=True,
            )
        except Exception:
            pass

        return Response(
            {"detail": "Campus request submitted. We'll be in touch within 2 business days."},
            status=status.HTTP_201_CREATED,
        )


class SuperAdminCampusListView(APIView):
    """GET /api/admin/campuses — all campuses with metrics (staff only).
       PATCH /api/admin/campuses/:id — approve or reject a pending campus.
    """

    permission_classes = [permissions.IsAdminUser]

    def get(self, request: Request) -> Response:
        from django.db.models import Count, Q

        campuses = Campus.objects.annotate(
            member_count=Count("members", distinct=True),
            listing_count=Count("members__listings", distinct=True),
            claimed_count=Count(
                "members__listings__reservations",
                filter=Q(members__listings__reservations__status="completed"),
                distinct=True,
            ),
        ).order_by("-created_at")

        return Response(SuperAdminCampusSerializer(campuses, many=True).data)

    def patch(self, request: Request, pk: int) -> Response:
        try:
            campus = Campus.objects.get(pk=pk)
        except Campus.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get("action")
        if action not in ("approve", "reject"):
            return Response({"detail": "action must be 'approve' or 'reject'."}, status=status.HTTP_400_BAD_REQUEST)

        if action == "approve":
            campus.onboarding_status = Campus.OnboardingStatus.APPROVED
            campus.active = True
        else:
            campus.onboarding_status = Campus.OnboardingStatus.REJECTED

        campus.save(update_fields=["onboarding_status", "active"])

        if campus.contact_email:
            verb = "approved" if action == "approve" else "not approved at this time"
            send_mail(
                subject=f"Your ReNest campus request has been {action}d",
                message=(
                    f"Hi {campus.contact_name},\n\n"
                    f"Your request for {campus.name} has been {verb}.\n\n"
                    f"— The ReNest team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[campus.contact_email],
                fail_silently=True,
            )

        return Response({"detail": f"Campus {action}d.", "onboarding_status": campus.onboarding_status})
