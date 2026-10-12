from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest

from .models import (
    AgentPolicy,
    AgentTransaction,
    AuditLog,
    IdempotencyKey,
    Investigation,
    PaymentEvent,
    PaymentTransaction,
    RiskEvaluation,
    RiskPolicy,
    SimulationRun,
)
from .services.policy import get_active_policy, update_sensitivity


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "amount", "currency", "decision", "status", "risk_score", "is_simulation", "created_at")
    list_filter = ("decision", "status", "is_simulation", "currency")
    search_fields = ("public_id", "merchant_id", "device_id", "request_id")
    readonly_fields = ("public_id", "created_at", "updated_at")
    date_hierarchy = "created_at"


@admin.register(RiskEvaluation)
class RiskEvaluationAdmin(admin.ModelAdmin):
    list_display = ("id", "transaction", "score", "decision", "sensitivity", "model_version", "created_at")
    list_filter = ("decision", "model_version")
    readonly_fields = ("created_at",)


@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    list_display = ("sequence", "event_id", "event_type", "entity_id", "status", "attempts", "occurred_at")
    list_filter = ("event_type", "status")
    search_fields = ("event_id", "entity_id", "request_id")
    readonly_fields = ("event_id", "sequence", "occurred_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "target_type", "target_id", "decision")
    list_filter = ("action", "target_type")
    search_fields = ("target_id", "actor_label", "request_id")

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("key", "user", "scope", "status", "response_status", "created_at", "expires_at")
    list_filter = ("status", "scope")
    search_fields = ("key",)


class RiskPolicyAdminForm(forms.ModelForm):
    reason = forms.CharField(
        required=False,
        max_length=300,
        help_text="Why the sensitivity is changing. Recorded in the audit log.",
    )

    class Meta:
        model = RiskPolicy
        fields = ("name", "sensitivity")

    def clean_sensitivity(self) -> int:
        value = int(self.cleaned_data["sensitivity"])
        if not 0 <= value <= 100:
            raise forms.ValidationError("Sensitivity must be between 0 and 100.")
        return value


@admin.register(RiskPolicy)
class RiskPolicyAdmin(admin.ModelAdmin):
    """The live policy decides which payments are blocked, so the admin is
    not allowed to be a side door around the audited path.

    A sensitivity change made here goes through `update_sensitivity`, the
    same service the API uses: it writes the audit row, emits
    risk.policy_updated and invalidates the cached value. A plain model save
    would do none of that, and payments would keep using the old thresholds
    until the cache expired with no record of who changed them. Adding,
    deleting and (de)activating rows changes which policy is live without
    any of those either, so the admin does not offer them.
    """

    form = RiskPolicyAdminForm
    list_display = ("name", "sensitivity", "active", "updated_by", "updated_at")
    readonly_fields = ("active", "updated_by", "updated_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(self, request: HttpRequest, obj: RiskPolicy, form: Any, change: bool) -> None:
        live = obj.active and obj.pk == get_active_policy().pk
        if not (live and "sensitivity" in form.changed_data):
            # Renaming, or editing a row that is not the live policy: nothing
            # payments read changes, so there is nothing to audit.
            super().save_model(request, obj, form, change)
            return
        update_sensitivity(
            actor=request.user,
            sensitivity=obj.sensitivity,
            reason=form.cleaned_data.get("reason") or "Changed in the Django admin",
            request_id=str(getattr(request, "request_id", "") or "")[:64],
        )
        if "name" in form.changed_data:
            # Only the name: saving the whole object would write this form's
            # stale updated_by over the one the service just recorded.
            obj.save(update_fields=["name", "updated_at"])


@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "scenario", "sensitivity", "transaction_count", "duration_ms", "created_at")
    list_filter = ("scenario",)


@admin.register(AgentPolicy)
class AgentPolicyAdmin(admin.ModelAdmin):
    list_display = ("public_id", "name", "user", "daily_limit", "transaction_limit", "active")


@admin.register(AgentTransaction)
class AgentTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "agent", "amount", "category", "decision", "created_at")
    list_filter = ("decision",)


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "intent", "mode", "latency_ms", "created_at")
    list_filter = ("intent", "mode")
