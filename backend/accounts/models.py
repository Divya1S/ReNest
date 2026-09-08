import secrets

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class Campus(models.Model):
    """A university campus. Users and listings are scoped to a campus."""

    class SubscriptionTier(models.TextChoices):
        FREE = "free", "Free"
        STANDARD = "standard", "Standard ($199/semester)"
        PREMIUM = "premium", "Premium ($399/semester)"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=60, unique=True, db_index=True)
    email_domains = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Comma-separated list of .edu domains for this campus.",
    )
    timezone = models.CharField(max_length=60, default="America/Los_Angeles")
    move_out_start = models.DateField(null=True, blank=True)
    move_out_end = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    # Phase 17 — Revenue & Sustainability
    subscription_tier = models.CharField(
        max_length=16,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE,
        db_index=True,
    )
    license_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the current paid license expires. Null = free tier.",
    )
    billing_email = models.EmailField(
        blank=True,
        help_text="Invoice recipient for campus license billing.",
    )
    stripe_customer_id = models.CharField(max_length=64, blank=True)
    # Phase 19 — Multi-Campus Expansion
    class OnboardingStatus(models.TextChoices):
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    onboarding_status = models.CharField(
        max_length=16,
        choices=OnboardingStatus.choices,
        default=OnboardingStatus.APPROVED,
        db_index=True,
    )
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    last_digest_sent_at = models.DateTimeField(null=True, blank=True)
    annual_report = models.FileField(upload_to="campus-reports/", null=True, blank=True)
    # Phase 27 — White-label theming (primary_color, logo_url, font_family, hero_message)
    theme = models.JSONField(
        null=True,
        blank=True,
        help_text="White-label theme overrides. Keys: primary_color, logo_url, font_family, hero_message.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "campuses"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.email_domains.split(",") if d.strip()]

    @classmethod
    def match_for_email(cls, email: str) -> "Campus | None":
        """Exact-domain campus match for onboarding.

        Compares the email's domain against each active campus's parsed domain
        list — never substring matching (icontains would let "myusc.edu"
        claim "usc.edu" addresses). If two campuses claim the same domain
        (a config error), the first by name wins deterministically instead of
        erroring or silently assigning none.
        """
        domain = email.rsplit("@", 1)[-1].strip().lower()
        if not domain:
            return None
        for campus in cls.objects.filter(active=True).order_by("name"):
            if domain in campus.domain_list:
                return campus
        return None

    @property
    def is_paid(self) -> bool:
        from django.utils import timezone
        return (
            self.subscription_tier != self.SubscriptionTier.FREE
            and (self.license_expires_at is None or self.license_expires_at > timezone.now())
        )


class CampusSubscription(models.Model):
    """Audit log of Stripe subscription events for a campus."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="subscriptions")
    stripe_session_id = models.CharField(max_length=120, blank=True)
    tier = models.CharField(max_length=16)
    amount_cents = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # Stripe retries webhooks; a session may only be recorded once.
            models.UniqueConstraint(
                fields=["stripe_session_id"],
                condition=~models.Q(stripe_session_id=""),
                name="unique_campus_subscription_session",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.campus} — {self.tier} ({self.created_at:%Y-%m-%d})"


class UserManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        """Lower-case the whole address, not just the domain.

        Django's default only normalises the domain, which would let
        Jules@usc.edu and jules@usc.edu become two accounts that each fail to
        log in as the other. Mail providers treat the local part
        case-insensitively in practice, so ReNest stores one canonical form.
        """
        return super().normalize_email(email or "").strip().lower()

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


def _gen_referral_code() -> str:
    return secrets.token_urlsafe(6)[:8]


class User(AbstractUser):
    class Milestone(models.TextChoices):
        NONE = "", "None"
        FIRST_RESCUE = "first_rescue", "First Rescue"
        ACTIVE_RESCUER = "active_rescuer", "Active Rescuer"
        CAMPUS_HERO = "campus_hero", "Campus Hero"

    username = None
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=120)
    campus_name = models.CharField(max_length=120, blank=True)
    campus = models.ForeignKey(
        Campus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    email_verified = models.BooleanField(default=False)
    milestone = models.CharField(
        max_length=24,
        choices=Milestone.choices,
        default=Milestone.NONE,
        blank=True,
    )
    referral_code = models.CharField(
        max_length=12,
        unique=True,
        default=_gen_referral_code,
        db_index=True,
    )
    referral_count = models.PositiveIntegerField(default=0)
    is_campus_manager = models.BooleanField(
        default=False,
        help_text="Grants access to the campus partner analytics dashboard.",
    )
    email_notifications = models.BooleanField(default=True)
    trust_strikes = models.PositiveSmallIntegerField(
        default=0,
        help_text="Incremented on: owner cancels confirmed reservation, confirmed report, dispute opened against user.",
    )
    category_affinity = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-category interaction weight vector, recomputed by Celery after every 10 interactions.",
    )
    onboarding_step = models.PositiveSmallIntegerField(
        default=0,
        help_text="0=incomplete, 1=profile_done, 2=first_listing, 3=complete",
    )
    # Phase 21 — Social & Community
    show_on_leaderboard = models.BooleanField(
        default=True,
        help_text="Opt-in to the campus leaderboard. Default True.",
    )
    # Phase 18 — completion rate as a trust signal
    completion_rate = models.FloatField(
        null=True,
        blank=True,
        help_text="completed_reservations / total_reservations_as_owner. Recomputed weekly.",
    )
    referred_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrals",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.display_name or self.email
