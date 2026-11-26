from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from django.db import transaction

from apps.ingestion.models import Document
from apps.ingestion.storage import save_bytes
from apps.tenancy.models import Tenant
from apps.topics.models import Topic


@dataclass(frozen=True)
class DocumentIntakeResult:
    document: Document
    deduplicated: bool


@dataclass(frozen=True)
class ManualTextIntakeResult:
    parent: DocumentIntakeResult
    children: list[DocumentIntakeResult]


def format_manual_text_markdown(title: str, content: str) -> str:
    clean_title = title.strip()
    clean_content = content.strip()
    if clean_title:
        return f"# {clean_title}\n\n{clean_content}\n"
    return f"{clean_content}\n"


def create_file_document(
    *,
    tenant: Tenant,
    topic: Topic,
    filename: str,
    raw_bytes: bytes,
    force_reindex: bool = False,
    parent_document: Document | None = None,
) -> DocumentIntakeResult:
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    if not force_reindex:
        existing = (
            Document.objects.for_tenant(tenant)
            .filter(topic=topic, content_hash=content_hash, status=Document.Status.INDEXED)
            .first()
        )
        if existing:
            return DocumentIntakeResult(document=existing, deduplicated=True)

    doc_id = uuid.uuid4()
    safe_name = filename or "upload.bin"
    raw_key = f"tenants/{tenant.id}/topics/{topic.id}/documents/{doc_id}/raw/{safe_name}"
    saved_key = save_bytes(raw_key, raw_bytes)

    doc = Document.objects.create(
        id=doc_id,
        tenant=tenant,
        topic=topic,
        parent_document=parent_document,
        source_type=Document.SourceType.FILE,
        source_uri=safe_name,
        raw_storage_key=saved_key,
        content_hash=content_hash,
        status=Document.Status.PENDING,
        schema_version_applied=topic.schema_version,
    )
    return DocumentIntakeResult(document=doc, deduplicated=False)


def create_manual_text_with_attachments(
    *,
    tenant: Tenant,
    topic: Topic,
    title: str,
    content: str,
    attachments: list[tuple[str, bytes]] | None = None,
    force_reindex: bool = False,
) -> ManualTextIntakeResult:
    markdown_body = format_manual_text_markdown(title, content)
    raw_bytes = markdown_body.encode("utf-8")
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    source_uri = title.strip() or "Manual Text Submission"

    with transaction.atomic():
        parent_result: DocumentIntakeResult | None = None
        if not force_reindex:
            existing_parent = (
                Document.objects.for_tenant(tenant)
                .filter(topic=topic, content_hash=content_hash, status=Document.Status.INDEXED)
                .first()
            )
            if existing_parent:
                parent_result = DocumentIntakeResult(document=existing_parent, deduplicated=True)

        if parent_result is None:
            doc_id = uuid.uuid4()
            raw_key = f"tenants/{tenant.id}/topics/{topic.id}/documents/{doc_id}/raw/submission.md"
            saved_key = save_bytes(raw_key, raw_bytes)
            parent_doc = Document.objects.create(
                id=doc_id,
                tenant=tenant,
                topic=topic,
                source_type=Document.SourceType.TEXT,
                source_uri=source_uri,
                raw_storage_key=saved_key,
                content_hash=content_hash,
                status=Document.Status.PENDING,
                schema_version_applied=topic.schema_version,
            )
            parent_result = DocumentIntakeResult(document=parent_doc, deduplicated=False)

        child_results: list[DocumentIntakeResult] = []
        for att_filename, att_bytes in attachments or []:
            child_res = create_file_document(
                tenant=tenant,
                topic=topic,
                filename=att_filename,
                raw_bytes=att_bytes,
                force_reindex=force_reindex,
                parent_document=parent_result.document,
            )
            child_results.append(child_res)

    return ManualTextIntakeResult(parent=parent_result, children=child_results)


def collect_document_storage_keys(doc: Document) -> list[str]:
    """Collect raw and normalized storage keys for a document and any child documents."""
    keys: list[str] = []
    if doc.raw_storage_key:
        keys.append(doc.raw_storage_key)
    if doc.normalized_storage_key:
        keys.append(doc.normalized_storage_key)
    for child in doc.child_documents.all():
        if child.raw_storage_key:
            keys.append(child.raw_storage_key)
        if child.normalized_storage_key:
            keys.append(child.normalized_storage_key)
    return keys
