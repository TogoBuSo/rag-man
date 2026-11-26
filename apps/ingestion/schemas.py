from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema
from pydantic import Field


class DocumentUrlIn(Schema):
    url: str
    force_reindex: bool = False


class DocumentIntakeOut(Schema):
    document_id: UUID
    status: str
    deduplicated: bool = False


class ManualTextIntakeOut(Schema):
    document_id: UUID
    status: str
    deduplicated: bool = False
    attachments: list[DocumentIntakeOut] = Field(default_factory=list)


class ChildDocumentSummaryOut(Schema):
    id: UUID
    source_type: str
    source_uri: str
    status: str
    semantic_score: float | None
    created_at: datetime


class DocumentOut(Schema):
    id: UUID
    topic_id: UUID
    parent_document_id: UUID | None = None
    source_type: str
    source_uri: str
    content_hash: str
    status: str
    schema_version_applied: int
    semantic_score: float | None
    extracted_metadata: dict[str, Any]
    validation_report: dict[str, Any]
    child_documents: list[ChildDocumentSummaryOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
