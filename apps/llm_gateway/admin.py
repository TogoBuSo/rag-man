from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from apps.llm_gateway.models import LLMUsageLog


@admin.register(LLMUsageLog)
class LLMUsageLogAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "topic",
        "operation",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "estimated_cost_usd",
        "created_at",
    )
    list_filter = ("operation", "model", "tenant", "created_at")
    search_fields = ("model", "tenant__name", "topic__name")
    readonly_fields = (
        "id",
        "tenant",
        "topic",
        "operation",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "estimated_cost_usd",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: LLMUsageLog | None = None) -> bool:
        return False
