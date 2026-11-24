from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field


class TopicCreateIn(Schema):
    slug: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    system_description: str = Field(..., min_length=1)
    glossary: dict[str, str] = Field(default_factory=dict)
    semantic_rules: dict[str, list[str]] = Field(default_factory=dict)
    min_semantic_score: float = Field(default=0.75, ge=0.0, le=1.0)
    metadata_schema: dict[str, Any] = Field(default_factory=dict)
    failure_policy: Literal["STRICT_REJECT", "QUARANTINE"] = "QUARANTINE"
    chunking_config: dict[str, Any] | None = None
    synthesis_prompt: str = ""
    extraction_model: str | None = None
    synthesis_model: str | None = None


class TopicUpdateIn(Schema):
    name: str | None = None
    is_active: bool | None = None
    system_description: str | None = None
    glossary: dict[str, str] | None = None
    semantic_rules: dict[str, list[str]] | None = None
    min_semantic_score: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata_schema: dict[str, Any] | None = None
    failure_policy: Literal["STRICT_REJECT", "QUARANTINE"] | None = None
    chunking_config: dict[str, Any] | None = None
    synthesis_prompt: str | None = None
    extraction_model: str | None = None
    synthesis_model: str | None = None


class TopicOut(Schema):
    id: UUID
    slug: str
    name: str
    is_active: bool
    system_description: str
    glossary: dict[str, Any]
    semantic_rules: dict[str, Any]
    min_semantic_score: float
    metadata_schema: dict[str, Any]
    schema_version: int
    failure_policy: str
    chunking_config: dict[str, Any]
    synthesis_prompt: str
    extraction_model: str | None
    synthesis_model: str | None
    created_at: datetime
    updated_at: datetime
