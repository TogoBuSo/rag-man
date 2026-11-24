from __future__ import annotations

import hashlib
import uuid
from uuid import UUID

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import File, Form, Router, UploadedFile

from apps.ingestion.models import Document
from apps.ingestion.schemas import DocumentIntakeOut, DocumentOut, DocumentUrlIn
from apps.ingestion.storage import delete_key, save_bytes
from apps.ingestion.tasks import dispatch_ingestion_pipeline
from apps.topics.models import Topic

router = Router()


@router.post("/topics/{topic_id}/documents/upload", response={200: DocumentIntakeOut, 202: DocumentIntakeOut})
def upload_document_endpoint(
    request: HttpRequest,
    topic_id: UUID,
    file: UploadedFile = File(...),
    force_reindex: bool = Form(False),
) -> tuple[int, DocumentIntakeOut]:
    tenant = request.tenant  # type: ignore[attr-defined]
    topic = get_object_or_404(Topic.objects.for_tenant(tenant), id=topic_id, is_active=True)

    raw_bytes = file.read()
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    if not force_reindex:
        existing = (
            Document.objects.for_tenant(tenant)
            .filter(topic=topic, content_hash=content_hash, status=Document.Status.INDEXED)
            .first()
        )
        if existing:
            return 200, DocumentIntakeOut(
                document_id=existing.id,
                status=existing.status,
                deduplicated=True,
            )

    doc_id = uuid.uuid4()
    filename = file.name or "upload.bin"
    raw_key = f"tenants/{tenant.id}/topics/{topic.id}/documents/{doc_id}/raw/{filename}"
    saved_key = save_bytes(raw_key, raw_bytes)

    doc = Document.objects.create(
        id=doc_id,
        tenant=tenant,
        topic=topic,
        source_type=Document.SourceType.FILE,
        source_uri=filename,
        raw_storage_key=saved_key,
        content_hash=content_hash,
        status=Document.Status.PENDING,
        schema_version_applied=topic.schema_version,
    )
    dispatch_ingestion_pipeline(str(doc.id))
    return 202, DocumentIntakeOut(document_id=doc.id, status=doc.status, deduplicated=False)


@router.post("/topics/{topic_id}/documents/url", response={200: DocumentIntakeOut, 202: DocumentIntakeOut})
def ingest_url_endpoint(
    request: HttpRequest,
    topic_id: UUID,
    payload: DocumentUrlIn,
) -> tuple[int, DocumentIntakeOut]:
    tenant = request.tenant  # type: ignore[attr-defined]
    topic = get_object_or_404(Topic.objects.for_tenant(tenant), id=topic_id, is_active=True)

    content_hash = hashlib.sha256(payload.url.strip().encode("utf-8")).hexdigest()

    if not payload.force_reindex:
        existing = (
            Document.objects.for_tenant(tenant)
            .filter(topic=topic, content_hash=content_hash, status=Document.Status.INDEXED)
            .first()
        )
        if existing:
            return 200, DocumentIntakeOut(
                document_id=existing.id,
                status=existing.status,
                deduplicated=True,
            )

    doc_id = uuid.uuid4()
    raw_key = f"tenants/{tenant.id}/topics/{topic.id}/documents/{doc_id}/raw/url.txt"
    saved_key = save_bytes(raw_key, payload.url.strip().encode("utf-8"))

    doc = Document.objects.create(
        id=doc_id,
        tenant=tenant,
        topic=topic,
        source_type=Document.SourceType.URL,
        source_uri=payload.url.strip(),
        raw_storage_key=saved_key,
        content_hash=content_hash,
        status=Document.Status.PENDING,
        schema_version_applied=topic.schema_version,
    )
    dispatch_ingestion_pipeline(str(doc.id))
    return 202, DocumentIntakeOut(document_id=doc.id, status=doc.status, deduplicated=False)


@router.get("/documents/{document_id}", response=DocumentOut)
def get_document_endpoint(request: HttpRequest, document_id: UUID) -> DocumentOut:
    doc = get_object_or_404(Document.objects.for_tenant(request.tenant), id=document_id)  # type: ignore[attr-defined]
    return DocumentOut.model_validate(doc, from_attributes=True)


@router.delete("/documents/{document_id}", response={204: None})
def delete_document_endpoint(request: HttpRequest, document_id: UUID) -> tuple[int, None]:
    doc = get_object_or_404(Document.objects.for_tenant(request.tenant), id=document_id)  # type: ignore[attr-defined]
    raw_key = doc.raw_storage_key
    normalized_key = doc.normalized_storage_key
    doc.delete()
    delete_key(raw_key)
    delete_key(normalized_key)
    return 204, None
