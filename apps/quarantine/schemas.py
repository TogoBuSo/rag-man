from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema


class QuarantineReviewOut(Schema):
    id: UUID
    document_id: UUID
    topic_id: UUID
    source_uri: str
    failure_type: str
    suggested_topics: list[dict[str, Any]]
    proposed_metadata: dict[str, Any]
    validation_report: dict[str, Any]
    resolution: str
    resolution_notes: str
    resolved_at: datetime | None
    created_at: datetime


class QuarantineResolveIn(Schema):
    action: Literal["APPROVED_OVERRIDE", "EDITED_AND_INDEXED", "REASSIGNED", "REJECTED"]
    edited_metadata: dict[str, Any] | None = None
    target_topic_id: UUID | None = None
    resolution_notes: str = ""
