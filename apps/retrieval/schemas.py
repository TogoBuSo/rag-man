from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field


class MetadataFilter(Schema):
    field: str = Field(..., min_length=1, max_length=128)
    operator: Literal["eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"] = "eq"
    value: Any


class RetrieveRequest(Schema):
    query: str = Field(..., min_length=1)
    topic_ids: list[UUID] = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    hybrid_alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata_filters: list[MetadataFilter] = Field(default_factory=list)
    rerank: bool = False


class RetrievedChunkOut(Schema):
    chunk_id: UUID
    document_id: UUID
    topic_id: UUID
    topic_name: str
    source_uri: str
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None


class SynthesizeRequest(RetrieveRequest):
    stream: bool = False


class SynthesizeResponse(Schema):
    answer: str
    citations: list[RetrievedChunkOut]
