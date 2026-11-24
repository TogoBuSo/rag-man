from __future__ import annotations

from uuid import UUID

import jsonschema
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja.errors import HttpError

from apps.ingestion.models import Document
from apps.ingestion.storage import cleanup_temp_files
from apps.ingestion.tasks import dispatch_index_only, dispatch_validation_and_index
from apps.quarantine.models import QuarantineReview
from apps.quarantine.schemas import QuarantineResolveIn, QuarantineReviewOut
from apps.tenancy.models import Tenant
from apps.topics.models import Topic


def _to_out(review: QuarantineReview) -> QuarantineReviewOut:
    return QuarantineReviewOut(
        id=review.id,
        document_id=review.document_id,
        topic_id=review.document.topic_id,
        source_uri=review.document.source_uri,
        failure_type=review.failure_type,
        suggested_topics=review.suggested_topics,
        proposed_metadata=review.proposed_metadata,
        validation_report=review.document.validation_report,
        resolution=review.resolution,
        resolution_notes=review.resolution_notes,
        resolved_at=review.resolved_at,
        created_at=review.created_at,
    )


def list_pending_reviews(tenant: Tenant) -> list[QuarantineReviewOut]:
    reviews = (
        QuarantineReview.objects.for_tenant(tenant)
        .filter(resolution=QuarantineReview.Resolution.PENDING)
        .select_related("document", "document__topic")
        .order_by("created_at")
    )
    return [_to_out(r) for r in reviews]


def resolve_quarantine_review(
    tenant: Tenant, review_id: UUID, payload: QuarantineResolveIn
) -> QuarantineReviewOut:
    review = get_object_or_404(
        QuarantineReview.objects.for_tenant(tenant).select_related("document", "document__topic"),
        id=review_id,
    )
    if review.resolution != QuarantineReview.Resolution.PENDING:
        raise HttpError(409, f"Quarantine review is already resolved ({review.resolution}).")

    doc = review.document
    now = timezone.now()

    if payload.action == QuarantineReview.Resolution.APPROVED_OVERRIDE:
        with transaction.atomic():
            if not doc.extracted_metadata and review.proposed_metadata:
                doc.extracted_metadata = review.proposed_metadata
                doc.save(update_fields=["extracted_metadata", "updated_at"])
            review.resolution = QuarantineReview.Resolution.APPROVED_OVERRIDE
            review.resolution_notes = payload.resolution_notes
            review.resolved_at = now
            review.save(update_fields=["resolution", "resolution_notes", "resolved_at", "updated_at"])
        dispatch_index_only(str(doc.id))

    elif payload.action == QuarantineReview.Resolution.EDITED_AND_INDEXED:
        if payload.edited_metadata is None:
            raise HttpError(422, "edited_metadata is required for EDITED_AND_INDEXED.")
        if doc.topic.metadata_schema:
            try:
                jsonschema.validate(
                    instance=payload.edited_metadata,
                    schema=doc.topic.metadata_schema,
                    cls=jsonschema.Draft202012Validator,
                )
            except jsonschema.ValidationError as exc:
                raise HttpError(422, f"edited_metadata failed schema validation: {exc.message}") from exc

        with transaction.atomic():
            doc.extracted_metadata = payload.edited_metadata
            doc.schema_version_applied = doc.topic.schema_version
            doc.save(update_fields=["extracted_metadata", "schema_version_applied", "updated_at"])
            review.resolution = QuarantineReview.Resolution.EDITED_AND_INDEXED
            review.resolution_notes = payload.resolution_notes
            review.resolved_at = now
            review.save(update_fields=["resolution", "resolution_notes", "resolved_at", "updated_at"])
        dispatch_index_only(str(doc.id))

    elif payload.action == QuarantineReview.Resolution.REASSIGNED:
        if payload.target_topic_id is None:
            raise HttpError(422, "target_topic_id is required for REASSIGNED.")
        target_topic = get_object_or_404(
            Topic.objects.for_tenant(tenant), id=payload.target_topic_id, is_active=True
        )
        with transaction.atomic():
            doc.topic = target_topic
            doc.status = Document.Status.PENDING
            doc.extracted_metadata = {}
            doc.save(update_fields=["topic", "status", "extracted_metadata", "updated_at"])
            review.resolution = QuarantineReview.Resolution.REASSIGNED
            review.resolution_notes = payload.resolution_notes
            review.resolved_at = now
            review.save(update_fields=["resolution", "resolution_notes", "resolved_at", "updated_at"])
        dispatch_validation_and_index(str(doc.id))

    elif payload.action == QuarantineReview.Resolution.REJECTED:
        with transaction.atomic():
            doc.status = Document.Status.REJECTED
            doc.save(update_fields=["status", "updated_at"])
            review.resolution = QuarantineReview.Resolution.REJECTED
            review.resolution_notes = payload.resolution_notes
            review.resolved_at = now
            review.save(update_fields=["resolution", "resolution_notes", "resolved_at", "updated_at"])
        cleanup_temp_files(doc.raw_storage_key, doc.normalized_storage_key)

    review.refresh_from_db()
    return _to_out(review)
