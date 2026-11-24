from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


def default_chunking_config() -> dict[str, object]:
    return {
        "chunk_size": 800,
        "chunk_overlap": 120,
        "strategy": "markdown_recursive",
    }


class Topic(models.Model):
    class FailurePolicy(models.TextChoices):
        STRICT_REJECT = "STRICT_REJECT", "Strict Reject"
        QUARANTINE = "QUARANTINE", "Quarantine for Review"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="topics")
    slug = models.SlugField(max_length=128)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    # Semantic Context
    system_description = models.TextField()
    glossary = models.JSONField(default=dict, blank=True)
    semantic_rules = models.JSONField(default=dict, blank=True)  # {"include": [...], "exclude": [...]}
    min_semantic_score = models.FloatField(default=0.75)

    # Structured Metadata Contract (JSON Schema Draft 2020-12)
    metadata_schema = models.JSONField(default=dict)
    schema_version = models.PositiveIntegerField(default=1)

    # Pipeline & Synthesis Policy
    failure_policy = models.CharField(
        max_length=32, choices=FailurePolicy.choices, default=FailurePolicy.QUARANTINE
    )
    chunking_config = models.JSONField(default=default_chunking_config)
    synthesis_prompt = models.TextField(blank=True, default="")
    extraction_model = models.CharField(max_length=128, blank=True, null=True)
    synthesis_model = models.CharField(max_length=128, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        unique_together = [("tenant", "slug")]

    def __str__(self) -> str:
        return f"{self.tenant.slug}/{self.slug}"
