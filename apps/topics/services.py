from __future__ import annotations

from typing import Any
from uuid import UUID

import jsonschema
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from apps.tenancy.models import Tenant
from apps.topics.models import Topic, default_chunking_config
from apps.topics.schemas import TopicCreateIn, TopicUpdateIn


def validate_json_schema(schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict):
        raise HttpError(422, "metadata_schema must be a JSON object.")
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise HttpError(422, f"Invalid JSON Schema (Draft 2020-12): {exc.message}") from exc


def create_topic(tenant: Tenant, payload: TopicCreateIn) -> Topic:
    validate_json_schema(payload.metadata_schema)
    try:
        with transaction.atomic():
            return Topic.objects.create(
                tenant=tenant,
                slug=payload.slug,
                name=payload.name,
                system_description=payload.system_description,
                glossary=payload.glossary,
                semantic_rules=payload.semantic_rules,
                min_semantic_score=payload.min_semantic_score,
                metadata_schema=payload.metadata_schema,
                failure_policy=payload.failure_policy,
                chunking_config=payload.chunking_config or default_chunking_config(),
                synthesis_prompt=payload.synthesis_prompt,
                extraction_model=payload.extraction_model,
                synthesis_model=payload.synthesis_model,
            )
    except IntegrityError as exc:
        raise HttpError(409, f"Topic with slug '{payload.slug}' already exists for this tenant.") from exc


def list_active_topics(tenant: Tenant) -> list[Topic]:
    return list(Topic.objects.for_tenant(tenant).filter(is_active=True).order_by("created_at"))


def update_topic(tenant: Tenant, topic_id: UUID, payload: TopicUpdateIn) -> Topic:
    topic = get_object_or_404(Topic.objects.for_tenant(tenant), id=topic_id)
    updates = payload.model_dump(exclude_unset=True)

    if "metadata_schema" in updates:
        new_schema = updates["metadata_schema"]
        validate_json_schema(new_schema)
        if new_schema != topic.metadata_schema:
            topic.schema_version += 1

    for field, value in updates.items():
        setattr(topic, field, value)

    topic.save()
    return topic
