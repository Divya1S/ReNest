from django.urls import path

from .views import (
    AccountDeleteView,
    CampusOnboardingRequestView,
    CampusPublicStatsView,
    CsrfCookieView,
    DataExportView,
    EmailUnsubscribeView,
    ImpactCardView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    ReferralLinkView,
    RegisterView,
    SendVerificationEmailView,
    SuperAdminCampusListView,
    VerifyEmailConfirmView,
)

urlpatterns = [
    path("csrf", CsrfCookieView.as_view(), name="auth-csrf"),
    path("register", RegisterView.as_view(), name="auth-register"),
    path("login", LoginView.as_view(), name="auth-login"),
    path("logout", LogoutView.as_view(), name="auth-logout"),
    path("me", MeView.as_view(), name="auth-me"),
    path("referral-link", ReferralLinkView.as_view(), name="auth-referral-link"),
    path("password-reset", PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path("password-reset/confirm", PasswordResetConfirmView.as_view(), name="auth-password-reset-confirm"),
    path("verify-email/send", SendVerificationEmailView.as_view(), name="auth-verify-email-send"),
    path("verify-email/confirm", VerifyEmailConfirmView.as_view(), name="auth-verify-email-confirm"),
    path("unsubscribe", EmailUnsubscribeView.as_view(), name="auth-unsubscribe"),
    path("account", AccountDeleteView.as_view(), name="auth-account-delete"),
    path("data-export", DataExportView.as_view(), name="auth-data-export"),
    path("impact-card.png", ImpactCardView.as_view(), name="auth-impact-card"),
    # Phase 19 — Multi-Campus Expansion
    path("campus/onboard", CampusOnboardingRequestView.as_view(), name="campus-onboard"),
    path("campus/<slug:slug>/stats", CampusPublicStatsView.as_view(), name="campus-public-stats"),
    path("admin/campuses", SuperAdminCampusListView.as_view(), name="admin-campus-list"),
    path("admin/campuses/<int:pk>", SuperAdminCampusListView.as_view(), name="admin-campus-detail"),
]
