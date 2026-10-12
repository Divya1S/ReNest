from __future__ import annotations

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import serializers
from typing import Any

from .models import Campus, User


class MessageSerializer(serializers.Serializer):
    """Reusable `{"detail": "..."}` response shape."""
    detail = serializers.CharField()


class CampusSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campus
        fields = ("id", "name", "slug", "timezone", "move_out_start", "move_out_end")
        read_only_fields = fields


class CampusPublicStatsSerializer(serializers.Serializer):
    """Public campus profile stats (no auth required)."""
    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.SlugField()
    timezone = serializers.CharField()
    move_out_start = serializers.DateField(allow_null=True)
    move_out_end = serializers.DateField(allow_null=True)
    subscription_tier = serializers.CharField()
    listing_count = serializers.IntegerField()
    claimed_count = serializers.IntegerField()
    member_count = serializers.IntegerField()
    total_value_rescued = serializers.DecimalField(max_digits=12, decimal_places=2)
    top_categories = serializers.ListField(child=serializers.DictField())


class CampusOnboardingSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    slug = serializers.SlugField(max_length=60)
    email_domains = serializers.CharField(max_length=500)
    contact_name = serializers.CharField(max_length=200)
    contact_email = serializers.EmailField()
    timezone = serializers.CharField(max_length=60, default="America/Los_Angeles")

    def validate_slug(self, value: str) -> str:
        if Campus.objects.filter(slug=value).exists():
            raise serializers.ValidationError("A campus with this slug already exists.")
        return value


class SuperAdminCampusSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    listing_count = serializers.IntegerField(read_only=True)
    claimed_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Campus
        fields = (
            "id", "name", "slug", "active", "onboarding_status",
            "subscription_tier", "license_expires_at",
            "contact_name", "contact_email",
            "member_count", "listing_count", "claimed_count", "created_at",
        )
        read_only_fields = fields


class PublicUserSerializer(serializers.ModelSerializer):
    """The profile another user is allowed to see.

    Nested into listings, reservations, reports, updates and feeds. Never
    expose email or account flags here: those belong to /api/auth/me only.
    """

    class Meta:
        model = User
        fields = ("id", "display_name", "campus_name", "milestone", "is_campus_manager", "created_at")
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    campus = CampusSerializer(read_only=True)

    class Meta:
        model = User
        fields = (
            "id", "email", "display_name", "campus_name", "campus",
            "email_verified", "milestone", "referral_count", "is_campus_manager",
            "onboarding_step", "completion_rate", "show_on_leaderboard", "created_at",
        )
        read_only_fields = fields


# Same wording DRF's automatic UniqueValidator produced, so clients that show
# the field error see no change.
_EMAIL_TAKEN_MESSAGE = "user with this email already exists."


class RegisterSerializer(serializers.ModelSerializer):
    # Declared explicitly so ModelSerializer does not attach its automatic
    # UniqueValidator. That validator compares the raw input, before
    # validate_email lower-cases it, so "Jules@usc.edu" passed while
    # "jules@usc.edu" existed and the insert then died on the unique
    # constraint (HTTP 500). Uniqueness is checked in validate_email instead.
    email = serializers.EmailField(max_length=User._meta.get_field("email").max_length or 254)
    confirm_password = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("email", "display_name", "campus_name", "password", "confirm_password", "referral_code")

    def validate_email(self, value: str) -> str:
        # Accounts are keyed on the canonical lower-case address. The lookup
        # ignores case so a row stored with other casing (anything written
        # without going through the manager) also counts as taken.
        email = User.objects.normalize_email(value)
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(_EMAIL_TAKEN_MESSAGE)
        return email

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        allowed_domains: list[str] = getattr(settings, "CAMPUS_EMAIL_DOMAINS", [])
        if allowed_domains:
            domain = attrs["email"].split("@")[-1].lower()
            if domain not in [d.lower() for d in allowed_domains]:
                readable = ", ".join(allowed_domains)
                raise serializers.ValidationError(
                    {"email": f"Only {readable} email addresses may register."}
                )
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        # Pass the prospective user so UserAttributeSimilarityValidator can
        # reject passwords that echo the email or display name.
        validate_password(
            attrs["password"],
            User(
                email=attrs.get("email", ""),
                display_name=attrs.get("display_name", ""),
            ),
        )

        ref_code = attrs.pop("referral_code", "") or ""
        if ref_code:
            try:
                attrs["_referrer"] = User.objects.get(referral_code=ref_code)
            except User.DoesNotExist:
                pass

        # Auto-assign campus from the email domain (exact match; see
        # Campus.match_for_email for why icontains was wrong here).
        campus = Campus.match_for_email(attrs["email"])
        if campus is not None:
            attrs["_campus"] = campus
            if not attrs.get("campus_name"):
                attrs["campus_name"] = campus.name

        return attrs

    def create(self, validated_data: dict[str, Any]) -> User:
        validated_data.pop("confirm_password")
        referrer = validated_data.pop("_referrer", None)
        campus = validated_data.pop("_campus", None)
        try:
            # Savepoint: a failed insert must not poison an enclosing transaction.
            with transaction.atomic():
                user = User.objects.create_user(**validated_data)
        except IntegrityError:
            # Two sign-ups for one address can both pass validate_email; the
            # unique constraint stops the second. Report it as the same field
            # error instead of a 500. Anything else (a referral-code
            # collision, say) is a real error and is re-raised.
            if User.objects.filter(email__iexact=validated_data["email"]).exists():
                raise serializers.ValidationError({"email": [_EMAIL_TAKEN_MESSAGE]}) from None
            raise
        update_fields = []
        if referrer:
            user.referred_by = referrer
            update_fields.append("referred_by")
            # F() makes the database do the increment. The referrer row was
            # read during validation, so writing "that value + 1" back would
            # drop a concurrent sign-up that used the same referral code.
            User.objects.filter(pk=referrer.pk).update(referral_count=F("referral_count") + 1)
        if campus:
            user.campus = campus
            update_fields.append("campus")
        if update_fields:
            user.save(update_fields=update_fields)
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def validate_email(self, value: str) -> str:
        return User.objects.normalize_email(value)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return User.objects.normalize_email(value)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs: Any) -> Any:
        try:
            pk = force_str(urlsafe_base64_decode(attrs["uid"]))
            user = User.objects.get(pk=pk)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError({"uid": "Invalid reset link."})

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError(
                {"token": "This reset link has expired or already been used."}
            )

        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})

        validate_password(attrs["new_password"], user)
        attrs["user"] = user
        return attrs
