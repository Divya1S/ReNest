from __future__ import annotations

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
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
    """Public campus profile stats — no auth required."""
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


class RegisterSerializer(serializers.ModelSerializer):
    confirm_password = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("email", "display_name", "campus_name", "password", "confirm_password", "referral_code")

    def validate_email(self, value: str) -> str:
        # Accounts are keyed on the canonical lower-case address.
        return User.objects.normalize_email(value)

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

        # Auto-assign campus from the email domain (exact match — see
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
        user = User.objects.create_user(**validated_data)
        update_fields = []
        if referrer:
            user.referred_by = referrer
            update_fields.append("referred_by")
            User.objects.filter(pk=referrer.pk).update(referral_count=referrer.referral_count + 1)
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
