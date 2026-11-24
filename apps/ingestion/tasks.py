from __future__ import annotations

import logging
from typing import Any

import jsonschema
import litellm
from celery import chain, shared_task
from django.contrib.postgres.search import SearchVector
from django.db import transaction

from apps.ingestion.chunkers import chunk_markdown
from apps.ingestion.models import Document, DocumentChunk
from apps.ingestion.parsers import parse_file_to_markdown, parse_url_to_markdown
from apps.ingestion.storage import read_bytes, read_text, save_text
from apps.llm_gateway.client import llm_gateway
from apps.llm_gateway.models import LLMUsageLog
from apps.llm_gateway.prompts import (
    build_extraction_messages,
    build_semantic_gate_messages,
    build_topic_suggestion_messages,
)
from apps.quarantine.models import QuarantineReview
from apps.topics.models import Topic

logger = logging.getLogger(__name__)

EMBEDDING_BATCH_SIZE = 64


def dispatch_ingestion_pipeline(document_id: str) -> None:
    workflow = chain(
        parse_document_task.s(document_id).set(queue="parsing"),
        validate_and_extract_task.s().set(queue="llm_io"),
        index_document_task.s().set(queue="indexing"),
    )
    workflow.apply_async()


def dispatch_validation_and_index(document_id: str) -> None:
    workflow = chain(
        validate_and_extract_task.s(document_id).set(queue="llm_io"),
        index_document_task.s().set(queue="indexing"),
    )
    workflow.apply_async()


def dispatch_index_only(document_id: str) -> None:
    index_document_task.apply_async(args=[document_id], queue="indexing")


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def parse_document_task(self: Any, document_id: str) -> str:
    doc = Document.objects.select_related("tenant", "topic").get(id=document_id)
    doc.status = Document.Status.PARSING
    doc.save(update_fields=["status", "updated_at"])

    try:
        if doc.source_type == Document.SourceType.URL:
            markdown_text = parse_url_to_markdown(doc.source_uri)
        else:
            raw_bytes = read_bytes(doc.raw_storage_key)
            markdown_text = parse_file_to_markdown(doc.source_uri, raw_bytes)

        normalized_key = f"tenants/{doc.tenant_id}/topics/{doc.topic_id}/documents/{doc.id}/normalized.md"
        saved_key = save_text(normalized_key, markdown_text)
        doc.normalized_storage_key = saved_key
        doc.save(update_fields=["normalized_storage_key", "updated_at"])
        return str(doc.id)
    except Exception as exc:
        doc.status = Document.Status.FAILED
        doc.validation_report = {"stage": "parsing", "error": str(exc)}
        doc.save(update_fields=["status", "validation_report", "updated_at"])
        raise


@shared_task(
    bind=True,
    autoretry_for=(litellm.RateLimitError, litellm.APIConnectionError, litellm.Timeout),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def validate_and_extract_task(self: Any, document_id: str | None) -> str | None:
    if not document_id:
        return None

    doc = Document.objects.select_related("tenant", "topic").get(id=document_id)
    topic = doc.topic
    tenant = doc.tenant

    doc.status = Document.Status.VALIDATING
    doc.save(update_fields=["status", "updated_at"])

    markdown_text = read_text(doc.normalized_storage_key)

    # 1. Semantic Gatekeeping
    gate_result = llm_gateway.complete_json(
        tenant=tenant,
        topic=topic,
        operation=LLMUsageLog.Operation.SEMANTIC_GATE,
        messages=build_semantic_gate_messages(topic, markdown_text),
        role="extraction",
    )
    is_on_topic = bool(gate_result.get("is_on_topic", False))
    confidence_score = float(gate_result.get("confidence_score", 0.0))
    rationale = str(gate_result.get("rationale", ""))
    semantic_passed = is_on_topic and confidence_score >= topic.min_semantic_score

    # 2. LLM-Assisted Structured Metadata Extraction
    proposed_metadata: dict[str, Any] = {}
    schema_errors: list[str] = []
    schema_passed = True

    if topic.metadata_schema:
        try:
            proposed_metadata = llm_gateway.complete_json(
                tenant=tenant,
                topic=topic,
                operation=LLMUsageLog.Operation.EXTRACTION,
                messages=build_extraction_messages(topic, markdown_text),
                role="extraction",
                response_schema=topic.metadata_schema,
            )
            jsonschema.validate(
                instance=proposed_metadata,
                schema=topic.metadata_schema,
                cls=jsonschema.Draft202012Validator,
            )
        except (jsonschema.ValidationError, ValueError) as exc:
            schema_passed = False
            schema_errors.append(str(getattr(exc, "message", str(exc))))

    report: dict[str, Any] = {
        "semantic_gate": {
            "passed": semantic_passed,
            "is_on_topic": is_on_topic,
            "confidence_score": confidence_score,
            "min_required": topic.min_semantic_score,
            "rationale": rationale,
        },
        "schema_validation": {
            "passed": schema_passed,
            "schema_version": topic.schema_version,
            "errors": schema_errors,
        },
    }

    doc.semantic_score = confidence_score
    doc.schema_version_applied = topic.schema_version
    doc.validation_report = report

    # 3. Policy Gate
    if semantic_passed and schema_passed:
        doc.extracted_metadata = proposed_metadata
        doc.save(
            update_fields=[
                "semantic_score",
                "schema_version_applied",
                "extracted_metadata",
                "validation_report",
                "updated_at",
            ]
        )
        return str(doc.id)

    if not semantic_passed and not schema_passed:
        failure_type = QuarantineReview.FailureType.BOTH
    elif not semantic_passed:
        failure_type = QuarantineReview.FailureType.SEMANTIC_MISMATCH
    else:
        failure_type = QuarantineReview.FailureType.SCHEMA_VALIDATION

    if topic.failure_policy == Topic.FailurePolicy.STRICT_REJECT:
        doc.status = Document.Status.REJECTED
        doc.save(
            update_fields=[
                "status",
                "semantic_score",
                "schema_version_applied",
                "validation_report",
                "updated_at",
            ]
        )
        return None

    # Quarantine flow: optionally suggest alternative topics on semantic mismatch
    suggested_topics: list[dict[str, Any]] = []
    if not semantic_passed:
        other_topics = list(
            Topic.objects.for_tenant(tenant).filter(is_active=True).exclude(id=topic.id)
        )
        if other_topics:
            try:
                suggestion_resp = llm_gateway.complete_json(
                    tenant=tenant,
                    topic=topic,
                    operation=LLMUsageLog.Operation.SEMANTIC_GATE,
                    messages=build_topic_suggestion_messages(other_topics, markdown_text),
                    role="extraction",
                )
                raw_suggestions = suggestion_resp.get("suggested_topics", [])
                if isinstance(raw_suggestions, list):
                    suggested_topics = raw_suggestions
            except Exception as exc:
                logger.warning("Failed to compute suggested_topics for document %s: %s", doc.id, exc)

    doc.status = Document.Status.QUARANTINED
    doc.save(
        update_fields=[
            "status",
            "semantic_score",
            "schema_version_applied",
            "validation_report",
            "updated_at",
        ]
    )
    QuarantineReview.objects.update_or_create(
        document=doc,
        defaults={
            "tenant": tenant,
            "failure_type": failure_type,
            "suggested_topics": suggested_topics,
            "proposed_metadata": proposed_metadata,
            "resolution": QuarantineReview.Resolution.PENDING,
            "resolution_notes": "",
            "resolved_at": None,
        },
    )
    return None


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def index_document_task(self: Any, document_id: str | None) -> str | None:
    if not document_id:
        return None

    doc = Document.objects.select_related("tenant", "topic").get(id=document_id)
    doc.status = Document.Status.INDEXING
    doc.save(update_fields=["status", "updated_at"])

    markdown_text = read_text(doc.normalized_storage_key)
    chunk_items = chunk_markdown(markdown_text, doc.topic.chunking_config)

    embeddings: list[list[float]] = []
    for start in range(0, len(chunk_items), EMBEDDING_BATCH_SIZE):
        batch = [c.content for c in chunk_items[start : start + EMBEDDING_BATCH_SIZE]]
        embeddings.extend(
            llm_gateway.embed_texts(tenant=doc.tenant, topic=doc.topic, texts=batch)
        )

    chunk_models = [
        DocumentChunk(
            tenant=doc.tenant,
            topic=doc.topic,
            document=doc,
            chunk_index=item.chunk_index,
            content=item.content,
            token_count=item.token_count,
            embedding=vec,
            metadata={
                **doc.extracted_metadata,
                "heading": item.heading,
                "page": item.page,
            },
        )
        for item, vec in zip(chunk_items, embeddings, strict=True)
    ]

    with transaction.atomic():
        DocumentChunk.objects.for_tenant(doc.tenant).filter(document=doc).delete()
        if chunk_models:
            DocumentChunk.objects.bulk_create(chunk_models)
            DocumentChunk.objects.for_tenant(doc.tenant).filter(document=doc).update(
                search_vector=SearchVector("content", config="english")
            )
        doc.status = Document.Status.INDEXED
        doc.save(update_fields=["status", "updated_at"])

    return str(doc.id)
