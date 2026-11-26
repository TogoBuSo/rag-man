from __future__ import annotations

import hashlib
import uuid
from uuid import UUID

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import File, Form, Router, UploadedFile
from ninja.errors import HttpError

from apps.ingestion.models import Document
from apps.ingestion.schemas import (
    ChildDocumentSummaryOut,
    DocumentIntakeOut,
    DocumentOut,
    DocumentUrlIn,
    ManualTextIntakeOut,
)
from apps.ingestion.services import (
    collect_document_storage_keys,
    create_file_document,
    create_manual_text_with_attachments,
)
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

    result = create_file_document(
        tenant=tenant,
        topic=topic,
        filename=file.name or "upload.bin",
        raw_bytes=file.read(),
        force_reindex=force_reindex,
    )
    if result.deduplicated:
        return 200, DocumentIntakeOut(
            document_id=result.document.id,
            status=result.document.status,
            deduplicated=True,
        )

    dispatch_ingestion_pipeline(str(result.document.id))
    return 202, DocumentIntakeOut(
        document_id=result.document.id,
        status=result.document.status,
        deduplicated=False,
    )


@router.post("/topics/{topic_id}/documents/text", response={200: ManualTextIntakeOut, 202: ManualTextIntakeOut})
def ingest_manual_text_endpoint(
    request: HttpRequest,
    topic_id: UUID,
    title: str = Form(...),
    content: str = Form(...),
    force_reindex: bool = Form(False),
    attachments: list[UploadedFile] = File(default=[]),
) -> tuple[int, ManualTextIntakeOut]:
    tenant = request.tenant  # type: ignore[attr-defined]
    topic = get_object_or_404(Topic.objects.for_tenant(tenant), id=topic_id, is_active=True)

    if not title.strip():
        raise HttpError(422, "title must not be empty.")
    if not content.strip():
        raise HttpError(422, "content must not be empty.")

    attachment_payloads = [(f.name or "attachment.bin", f.read()) for f in attachments]
    intake = create_manual_text_with_attachments(
        tenant=tenant,
        topic=topic,
        title=title,
        content=content,
        attachments=attachment_payloads,
        force_reindex=force_reindex,
    )

    any_dispatched = False
    if not intake.parent.deduplicated:
        dispatch_ingestion_pipeline(str(intake.parent.document.id))
        any_dispatched = True

    attachment_outs: list[DocumentIntakeOut] = []
    for child in intake.children:
        if not child.deduplicated:
            dispatch_ingestion_pipeline(str(child.document.id))
            any_dispatched = True
        attachment_outs.append(
            DocumentIntakeOut(
                document_id=child.document.id,
                status=child.document.status,
                deduplicated=child.deduplicated,
            )
        )

    status_code = 202 if any_dispatched else 200
    return status_code, ManualTextIntakeOut(
        document_id=intake.parent.document.id,
        status=intake.parent.document.status,
        deduplicated=intake.parent.deduplicated,
        attachments=attachment_outs,
    )


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
    doc = get_object_or_404(
        Document.objects.for_tenant(request.tenant).prefetch_related("child_documents"),  # type: ignore[attr-defined]
        id=document_id,
    )
    children = [
        ChildDocumentSummaryOut.model_validate(c, from_attributes=True)
        for c in doc.child_documents.all().order_by("created_at")
    ]
    return DocumentOut(
        id=doc.id,
        topic_id=doc.topic_id,
        parent_document_id=doc.parent_document_id,
        source_type=doc.source_type,
        source_uri=doc.source_uri,
        content_hash=doc.content_hash,
        status=doc.status,
        schema_version_applied=doc.schema_version_applied,
        semantic_score=doc.semantic_score,
        extracted_metadata=doc.extracted_metadata,
        validation_report=doc.validation_report,
        child_documents=children,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.delete("/documents/{document_id}", response={204: None})
def delete_document_endpoint(request: HttpRequest, document_id: UUID) -> tuple[int, None]:
    doc = get_object_or_404(
        Document.objects.for_tenant(request.tenant).prefetch_related("child_documents"),  # type: ignore[attr-defined]
        id=document_id,
    )
    storage_keys = collect_document_storage_keys(doc)
    doc.delete()
    for key in storage_keys:
        delete_key(key)
    return 204, None
