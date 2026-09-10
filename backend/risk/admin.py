from __future__ import annotations

from typing import Any

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


@admin.register(RiskPolicy)
class RiskPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "sensitivity", "active", "updated_by", "updated_at")


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
