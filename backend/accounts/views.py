from __future__ import annotations

import logging
import uuid as _uuid

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import Campus, User
from dormcycle.parsers import StrictJSONParser
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


@method_decorator(csrf_protect, name="dispatch")
class CsrfProtectedAnonymousView(APIView):
    """Base for the endpoints an anonymous browser can use to change auth state.

    DRF wraps every view in csrf_exempt and its SessionAuthentication only
    checks the token for requests that already carry a logged-in session, so
    an anonymous POST (login, register, password reset) was never checked. A
    hostile page could then sign a victim's browser into an attacker's account
    (login CSRF). csrf_protect on dispatch is the pattern the DRF
    documentation gives for login views; subclasses inherit the wrapped
    dispatch. A refusal is rendered by settings.CSRF_FAILURE_VIEW.

    JSON only: a cross-site HTML form can post urlencoded or multipart bodies
    without a CORS preflight, but not application/json.
    """

    permission_classes = [permissions.AllowAny]
    parser_classes = [StrictJSONParser]


class RegisterView(CsrfProtectedAnonymousView):
    """POST /api/auth/register: create an account and start a session.

    JSON body; the X-CSRFToken header is required (GET /api/auth/csrf sets the cookie).
    """

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
            # leave a trace, because a silent drop here strands the user unverified.
            logger.warning("Verification email failed for %s", user.email, exc_info=True)
        else:
            if settings.DEBUG:
                # Console email backend in dev: surface the link in the UI so
                # local testers aren't forced to dig through server logs.
                payload["dev_verify_url"] = verify_url
        return Response(payload, status=status.HTTP_201_CREATED)


class LoginView(CsrfProtectedAnonymousView):
    """POST /api/auth/login: start a session with email and password.

    JSON body; the X-CSRFToken header is required (GET /api/auth/csrf sets the cookie).
    """

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


def _field_error(field: str, message: str) -> Response:
    """A 400 in the shape serializer errors use everywhere else: {field: [message]}.

    Clients read validation errors as lists; a bare string here made this one
    endpoint the exception they had to special-case.
    """
    return Response({field: [message]}, status=status.HTTP_400_BAD_REQUEST)


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
                "show_on_leaderboard": drf_serializers.BooleanField(required=False),
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
                return _field_error("display_name", "Display name cannot be empty.")
            if len(display_name) > 120:
                return _field_error("display_name", "Keep it under 120 characters.")
            request.user.display_name = display_name
            update_fields.append("display_name")

        campus_name = request.data.get("campus_name")
        if campus_name is not None:
            campus_name = str(campus_name).strip()
            if len(campus_name) > 120:
                return _field_error("campus_name", "Keep it under 120 characters.")
            request.user.campus_name = campus_name
            update_fields.append("campus_name")

        show_on_leaderboard = request.data.get("show_on_leaderboard")
        if show_on_leaderboard is not None:
            if not isinstance(show_on_leaderboard, bool):
                return _field_error("show_on_leaderboard", "Must be true or false.")
            request.user.show_on_leaderboard = show_on_leaderboard
            update_fields.append("show_on_leaderboard")

        step = request.data.get("onboarding_step")
        if step is not None:
            try:
                step = int(step)
            except (TypeError, ValueError):
                return _field_error("onboarding_step", "Must be an integer from 0 to 3.")
            if not 0 <= step <= 3:
                return _field_error("onboarding_step", "Must be between 0 and 3.")
            # Only allow advancing, not rolling back
            if step > request.user.onboarding_step:
                request.user.onboarding_step = step
                update_fields.append("onboarding_step")

        if update_fields:
            request.user.save(update_fields=update_fields)
        return Response({"authenticated": True, "user": UserSerializer(request.user).data})


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


class PasswordResetRequestView(CsrfProtectedAnonymousView):
    """POST /api/auth/password-reset: email a reset link if the address has an account.

    The response is the same either way, so it does not reveal who is registered.
    JSON body; the X-CSRFToken header is required (GET /api/auth/csrf sets the cookie).
    """

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

        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = (
                f"{settings.APP_BASE_URL}/password-reset/confirm?uid={uid}&token={token}"
            )
            try:
                send_mail(
                    subject="Reset your ReNest password",
                    message=(
                        f"Hi {user.display_name or user.email},\n\n"
                        f"Click the link below to reset your password. "
                        f"It expires in 1 hour.\n\n{reset_url}\n\n"
                        "If you didn't request this, you can safely ignore this email."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
            except Exception:
                # A mail-server failure must not turn into a 500 that tells the
                # caller this address is registered.
                logger.warning("Password reset email failed for %s", email, exc_info=True)

        return Response(
            {"detail": "If an account exists with that email, a reset link has been sent."}
        )


class PasswordResetConfirmView(CsrfProtectedAnonymousView):
    """POST /api/auth/password-reset/confirm: set a new password with the emailed uid and token.

    JSON body; the X-CSRFToken header is required (GET /api/auth/csrf sets the cookie).
    """

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


def make_unsubscribe_token(user: User) -> str:
    return signing.dumps(user.pk, salt=_UNSUBSCRIBE_SALT)


def make_unsubscribe_url(user: User) -> str:
    """Human-facing link: the SPA page that confirms the opt-out."""
    return f"{settings.APP_BASE_URL}/unsubscribe?token={make_unsubscribe_token(user)}"


def make_unsubscribe_api_url(user: User) -> str:
    """Machine-facing link for the RFC 8058 List-Unsubscribe header.

    Mail providers POST to this URL, so it must be the API endpoint (the SPA
    catch-all only answers GET).
    """
    return f"{settings.APP_BASE_URL}/api/auth/unsubscribe?token={make_unsubscribe_token(user)}"


class EmailUnsubscribeView(APIView):
    """
    GET/POST /api/auth/unsubscribe?token=...
    One-click unsubscribe link embedded in every transactional email.
    The token is a signed user PK (valid 90 days).

    POST is what RFC 8058 mail providers use for their one-click button; both
    verbs are idempotent and the signed token is the only credential, so no
    session or CSRF token is involved.
    """
    permission_classes = [permissions.AllowAny]
    authentication_classes: list[Any] = []

    def get(self, request: Request) -> Response:
        return self._unsubscribe(request)

    def post(self, request: Request) -> Response:
        return self._unsubscribe(request)

    def _unsubscribe(self, request: Request) -> Response:
        token = request.query_params.get("token", "") or str(request.data.get("token", ""))
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

    Anonymises PII, hard-deletes push subscriptions and the other personal
    records, soft-deletes the user's listings, then deactivates the account.
    GDPR Art. 17 right-to-erasure.

    Sessions: only the session making this request is deleted. Django does not
    index sessions by user, so the rows of sessions on other devices stay in
    the table until they expire. They stop authenticating immediately all the
    same, because the account is now inactive and its password unusable; what
    remains in such a row is the numeric id of an anonymised account.
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
    @transaction.atomic
    def delete(self, request: Request) -> Response:
        password = request.data.get("password", "")
        if not request.user.check_password(password):
            return Response({"detail": "Incorrect password."}, status=400)

        user = current_user(request)
        anon_id = _uuid.uuid4().hex[:12]

        from listings.models import (
            Listing,
            NotificationPreference,
            PushSubscription,
            Reservation,
            SavedListing,
            SavedSearch,
        )
        from listings.services import cancel_reservation

        # 1. Release anything the counterparty is waiting on: an account that
        #    disappears must not leave a listing stuck RESERVED forever.
        live = [Reservation.Status.REQUESTED, Reservation.Status.CONFIRMED]
        open_reservations = Reservation.objects.filter(
            Q(claimant=user) | Q(listing__owner=user), status__in=live
        ).select_related("listing")
        for reservation in open_reservations:
            try:
                cancel_reservation(reservation, previous_status=Reservation.Status.REQUESTED)
            except Exception:
                logger.exception("Could not cancel reservation %s during account deletion", reservation.pk)

        # 2. Soft-delete the user's listings and drop their uploaded files.
        owned = Listing.objects.filter(owner=user, deleted_at__isnull=True)
        for listing in owned.iterator(chunk_size=200):
            for image in listing.images.all():
                image.image.delete(save=False)
            if listing.image:
                listing.image.delete(save=False)
        owned.update(deleted_at=timezone.now())

        # 3. Delete the personal records that carry no value to anyone else.
        PushSubscription.objects.filter(user=user).delete()
        NotificationPreference.objects.filter(user=user).delete()
        SavedListing.objects.filter(user=user).delete()
        SavedSearch.objects.filter(user=user).delete()
        user.notifications.all().delete()
        try:
            from concierge.models import ConciergeThread

            ConciergeThread.objects.filter(user=user).delete()
        except Exception:
            logger.exception("Could not remove concierge history during account deletion")

        # 4. Anonymise what must stay for the counterparties' own records
        #    (handoff history, feedback), and lock the account out.
        User.objects.filter(pk=user.pk).update(
            email=f"deleted-{anon_id}@renest.invalid",
            display_name="Deleted User",
            campus_name="",
            campus=None,
            is_active=False,
            email_notifications=False,
            email_verified=False,
            referred_by=None,
            category_affinity={},
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        # 5. End the session on this device. Sessions elsewhere are already
        #    dead: step 4 made the account inactive (see the class docstring).
        request.session.flush()
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

        # No blanket try/except here: a query error must surface as a logged
        # 500, not as a plausible-looking but empty archive.
        from listings.models import (
            Listing,
            Notification,
            Reservation,
            ReservationMessage,
            RoomScanSession,
            SavedListing,
            SavedSearch,
        )

        payload = {
            "exported_at": timezone.now().isoformat(),
            "user": {
                "id": user.pk,
                "email": user.email,
                "display_name": user.display_name,
                "campus_name": user.campus_name,
                "campus": user.campus.name if user.campus else None,
                "email_verified": user.email_verified,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
            "listings": list(
                Listing.objects.filter(owner=user, is_demo=False).values(
                    "id", "title", "description", "category", "condition",
                    "status", "price_type", "price_amount", "pickup_zone",
                    "available_until", "created_at",
                )
            ),
            "reservations": list(
                Reservation.objects.filter(Q(claimant=user) | Q(listing__owner=user)).values(
                    "id", "status", "pickup_time_window", "created_at",
                    "listing__title", "listing__owner__display_name",
                )
            ),
            "messages": list(
                ReservationMessage.objects.filter(sender=user).values(
                    "id", "body", "created_at", "reservation_id"
                )
            ),
            "saved_listings": list(
                SavedListing.objects.filter(user=user).values("listing_id", "listing__title", "created_at")
            ),
            "saved_searches": list(
                SavedSearch.objects.filter(user=user).values(
                    "id", "label", "keyword", "category", "price_type", "created_at"
                )
            ),
            "scan_sessions": list(
                RoomScanSession.objects.filter(owner=user, is_demo=False).values(
                    "id", "name", "room_label", "status", "move_out_deadline", "created_at"
                )
            ),
            "notifications": list(
                Notification.objects.filter(user=user).values("id", "type", "title", "body", "created_at")
            ),
        }
        try:
            from concierge.models import ConciergeMessage

            payload["concierge_messages"] = list(
                ConciergeMessage.objects.filter(thread__user=user).values("id", "role", "content", "created_at")
            )
        except Exception:
            logger.exception("Concierge history could not be included in the data export")
            payload["concierge_messages"] = []

        return Response(payload)


# ---------- Phase 17: Impact Card ----------

_CATEGORY_WEIGHTS_LB = {
    "storage": 3.5,
    "lighting": 2.0,
    "supplies": 1.0,
    "comfort": 5.0,
    "toiletries": 0.5,
    "decor": 2.5,
    "other": 2.0,
}


# Fonts known to cover every character the card draws, in order of preference.
# Linux hosts (Render, the Docker image) have no Helvetica and often no system
# fonts at all, so the list is only an upgrade path: the renderer falls back
# to the scalable font bundled with Pillow.
_CARD_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian, Ubuntu
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",  # Fedora, Alpine
)


def _load_card_fonts(sizes: tuple[int, ...]) -> tuple[list[Any], bool]:
    """Fonts for the impact card at the given pixel sizes.

    Returns the fonts and whether they are a full Unicode face. Pillow's
    bundled font scales to any size but only covers basic Latin, so callers
    must keep to ASCII text when the flag is False (a missing glyph renders
    as an empty box).
    """
    from PIL import ImageFont

    for path in _CARD_FONT_CANDIDATES:
        try:
            return [ImageFont.truetype(path, size) for size in sizes], True
        except OSError:
            continue
    # load_default() without a size is a fixed 10px face, which made the whole
    # card unreadable wherever Helvetica was missing; ask for real sizes.
    return [ImageFont.load_default(size=size) for size in sizes], False


class ImpactCardView(APIView):
    """
    GET /api/auth/impact-card.png
    Renders a 1200x630 shareable impact summary card for the authenticated user.
    Drawn with Pillow; needs no font installed on the host (see _load_card_fonts).
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
        response["Content-Disposition"] = 'inline; filename="renest-impact.png"'
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

        (font_lg, font_md, font_sm, font_xs), full_unicode = _load_card_fonts((72, 36, 28, 22))

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
            (f"{co2_kg} kg", "CO₂ diverted" if full_unicode else "CO2 diverted"),
        ]
        col_w = (W - 120) // 3
        for i, (val, label) in enumerate(stats):
            x = 60 + i * col_w
            draw.text((x, 290), val, font=font_lg, fill=TEAL)
            draw.text((x, 380), label, font=font_sm, fill=MUTED)

        # Footer
        draw.text((60, H - 60), "renest.app  |  reduce move-out waste", font=font_xs, fill=MUTED)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


class CampusOnboardRateThrottle(AnonRateThrottle):
    scope = "campus_onboard"


class CampusPublicStatsView(APIView):
    """GET /api/auth/campus/<slug>/stats: public campus profile, no auth required."""

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
        top_categories = [{"category": r["listing__category"], "count": r["count"]} for r in top_cats]

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
    """POST /api/auth/campus/onboard: self-service campus registration (pending approval)."""

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

        admin_email = settings.PLATFORM_ADMIN_EMAIL
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
                    f"The ReNest team"
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
    """GET /api/auth/admin/campuses: all campuses with metrics (staff only).
       PATCH /api/auth/admin/campuses/<id>: approve or reject a pending campus.
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
            # A rejected campus must stop claiming new sign-ups by email domain.
            campus.active = False

        campus.save(update_fields=["onboarding_status", "active"])

        # Spelled out per action: f"{action}d" produced "rejectd".
        outcome = "approved" if action == "approve" else "rejected"

        if campus.contact_email:
            verb = "approved" if action == "approve" else "not approved at this time"
            send_mail(
                subject=f"Your ReNest campus request has been {outcome}",
                message=(
                    f"Hi {campus.contact_name},\n\n"
                    f"Your request for {campus.name} has been {verb}.\n\n"
                    f"The ReNest team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[campus.contact_email],
                fail_silently=True,
            )

        return Response({"detail": f"Campus {outcome}.", "onboarding_status": campus.onboarding_status})
