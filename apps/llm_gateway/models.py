from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class LLMUsageLog(models.Model):
    class Operation(models.TextChoices):
        SEMANTIC_GATE = "SEMANTIC_GATE", "Semantic Gate"
        EXTRACTION = "EXTRACTION", "Extraction"
        EMBEDDING = "EMBEDDING", "Embedding"
        SYNTHESIS = "SYNTHESIS", "Synthesis"
        RERANK = "RERANK", "Rerank"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="llm_usage_logs")
    topic = models.ForeignKey("topics.Topic", on_delete=models.SET_NULL, null=True, blank=True)
    operation = models.CharField(max_length=32, choices=Operation.choices, db_index=True)
    model = models.CharField(max_length=128)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "operation", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.tenant.slug}:{self.operation}:{self.model}"
