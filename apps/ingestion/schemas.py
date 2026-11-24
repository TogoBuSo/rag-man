from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema


class DocumentUrlIn(Schema):
    url: str
    force_reindex: bool = False


class DocumentIntakeOut(Schema):
    document_id: UUID
    status: str
    deduplicated: bool = False


class DocumentOut(Schema):
    id: UUID
    topic_id: UUID
    source_type: str
    source_uri: str
    content_hash: str
    status: str
    schema_version_applied: int
    semantic_score: float | None
    extracted_metadata: dict[str, Any]
    validation_report: dict[str, Any]
    created_at: datetime
    updated_at: datetime
