from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class QuarantineReview(models.Model):
    class FailureType(models.TextChoices):
        SEMANTIC_MISMATCH = "SEMANTIC_MISMATCH", "Semantic Mismatch"
        SCHEMA_VALIDATION = "SCHEMA_VALIDATION", "Schema Validation"
        BOTH = "BOTH", "Both"

    class Resolution(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED_OVERRIDE = "APPROVED_OVERRIDE", "Approved Override"
        EDITED_AND_INDEXED = "EDITED_AND_INDEXED", "Edited and Indexed"
        REASSIGNED = "REASSIGNED", "Reassigned"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="quarantine_reviews")
    document = models.OneToOneField(
        "ingestion.Document", on_delete=models.CASCADE, related_name="quarantine_review"
    )
    failure_type = models.CharField(max_length=32, choices=FailureType.choices)
    suggested_topics = models.JSONField(default=list, blank=True)
    proposed_metadata = models.JSONField(default=dict, blank=True)
    resolution = models.CharField(
        max_length=32, choices=Resolution.choices, default=Resolution.PENDING, db_index=True
    )
    resolution_notes = models.TextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "resolution"]),
        ]

    def __str__(self) -> str:
        return f"QuarantineReview({self.document_id}, {self.resolution})"
