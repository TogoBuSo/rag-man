from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField

from apps.tenancy.managers import TenantScopedManager


class Document(models.Model):
    class SourceType(models.TextChoices):
        FILE = "FILE", "File Upload"
        URL = "URL", "Web URL"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARSING = "PARSING", "Parsing"
        VALIDATING = "VALIDATING", "Validating"
        QUARANTINED = "QUARANTINED", "Quarantined"
        REJECTED = "REJECTED", "Rejected"
        INDEXING = "INDEXING", "Indexing"
        INDEXED = "INDEXED", "Indexed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, db_index=True)
    topic = models.ForeignKey("topics.Topic", on_delete=models.CASCADE, related_name="documents")

    source_type = models.CharField(max_length=16, choices=SourceType.choices)
    source_uri = models.TextField()
    raw_storage_key = models.CharField(max_length=512, blank=True, default="")
    normalized_storage_key = models.CharField(max_length=512, blank=True, default="")
    content_hash = models.CharField(max_length=64, db_index=True)

    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    schema_version_applied = models.PositiveIntegerField(default=1)
    semantic_score = models.FloatField(null=True, blank=True)
    extracted_metadata = models.JSONField(default=dict, blank=True)
    validation_report = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "topic", "content_hash"]),
            models.Index(fields=["tenant", "status"]),
        ]

    def __str__(self) -> str:
        return f"Document({self.id}, {self.status})"


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, db_index=True)
    topic = models.ForeignKey("topics.Topic", on_delete=models.CASCADE, db_index=True)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")

    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField()

    # Locked to system-wide uniform default embedding model dimension
    embedding = VectorField(dimensions=settings.DEFAULT_EMBEDDING_DIMENSIONS)
    search_vector = SearchVectorField(null=True)
    metadata = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "topic"], name="chunk_tenant_topic_idx"),
            GinIndex(fields=["metadata"], name="chunk_metadata_gin", opclasses=["jsonb_path_ops"]),
            GinIndex(fields=["search_vector"], name="chunk_fts_gin"),
            HnswIndex(
                name="chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"Chunk({self.document_id}#{self.chunk_index})"
