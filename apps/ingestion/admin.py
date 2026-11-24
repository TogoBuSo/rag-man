from __future__ import annotations

import hashlib
import uuid
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from apps.ingestion.models import Document, DocumentChunk
from apps.ingestion.storage import delete_key, save_bytes
from apps.ingestion.tasks import (
    dispatch_index_only,
    dispatch_ingestion_pipeline,
    run_ingestion_pipeline_sync,
)
from apps.topics.models import Topic


STATUS_COLORS: dict[str, str] = {
    Document.Status.PENDING: "#6c757d",
    Document.Status.PARSING: "#0dcaf0",
    Document.Status.VALIDATING: "#0d6efd",
    Document.Status.QUARANTINED: "#fd7e14",
    Document.Status.REJECTED: "#dc3545",
    Document.Status.INDEXING: "#6610f2",
    Document.Status.INDEXED: "#198754",
    Document.Status.FAILED: "#b02a37",
}


class DocumentCreateAdminForm(forms.ModelForm):
    topic = forms.ModelChoiceField(
        queryset=Topic.objects.select_related("tenant").filter(is_active=True).order_by("tenant__name", "name"),
        help_text="Select the target Topic (tenant is automatically inferred from the Topic).",
    )
    upload_file = forms.FileField(
        required=False,
        label="File to upload",
        help_text="Required when Source type is 'File Upload'. Uploaded to local temp storage (or S3 if enabled).",
    )
    source_url = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Web URL",
        help_text="Required when Source type is 'Web URL'.",
    )
    force_reindex = forms.BooleanField(
        required=False,
        initial=False,
        help_text="Re-ingest even if an indexed document with the same content hash already exists in this Topic.",
    )
    run_sync = forms.BooleanField(
        required=False,
        initial=False,
        label="Run immediately (sync)",
        help_text="Execute parse -> validate -> index synchronously in this request for debugging without a Celery worker.",
    )

    class Meta:
        model = Document
        fields = ["topic", "source_type"]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        topic: Topic | None = cleaned.get("topic")
        source_type = cleaned.get("source_type")
        upload_file = cleaned.get("upload_file")
        source_url = (cleaned.get("source_url") or "").strip()
        force_reindex = bool(cleaned.get("force_reindex"))

        if source_type == Document.SourceType.FILE:
            if not upload_file:
                self.add_error("upload_file", "Please select a file to upload.")
                return cleaned
            raw_bytes = upload_file.read()
            upload_file.seek(0)
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            cleaned["_raw_bytes"] = raw_bytes
            cleaned["_source_uri"] = upload_file.name or "upload.bin"
            cleaned["_content_hash"] = content_hash
        elif source_type == Document.SourceType.URL:
            if not source_url:
                self.add_error("source_url", "Please enter a valid HTTP/HTTPS URL.")
                return cleaned
            content_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
            cleaned["_raw_bytes"] = source_url.encode("utf-8")
            cleaned["_source_uri"] = source_url
            cleaned["_content_hash"] = content_hash

        if topic and "_content_hash" in cleaned and not force_reindex:
            existing = (
                Document.objects.for_tenant(topic.tenant)
                .filter(
                    topic=topic,
                    content_hash=cleaned["_content_hash"],
                    status=Document.Status.INDEXED,
                )
                .first()
            )
            if existing:
                raise forms.ValidationError(
                    f"An indexed document with identical content already exists in this topic "
                    f"(Document ID: {existing.id}). Check 'Force reindex' to ingest again."
                )

        return cleaned

    def save(self, commit: bool = True) -> Document:
        doc: Document = super().save(commit=False)
        topic: Topic = self.cleaned_data["topic"]
        source_type: str = self.cleaned_data["source_type"]
        raw_bytes: bytes = self.cleaned_data["_raw_bytes"]
        source_uri: str = self.cleaned_data["_source_uri"]
        content_hash: str = self.cleaned_data["_content_hash"]

        doc_id = uuid.uuid4()
        if source_type == Document.SourceType.FILE:
            raw_key = f"tenants/{topic.tenant_id}/topics/{topic.id}/documents/{doc_id}/raw/{source_uri}"
        else:
            raw_key = f"tenants/{topic.tenant_id}/topics/{topic.id}/documents/{doc_id}/raw/url.txt"

        saved_key = save_bytes(raw_key, raw_bytes)

        doc.id = doc_id
        doc.tenant = topic.tenant
        doc.topic = topic
        doc.source_type = source_type
        doc.source_uri = source_uri
        doc.raw_storage_key = saved_key
        doc.content_hash = content_hash
        doc.status = Document.Status.PENDING
        doc.schema_version_applied = topic.schema_version
        if commit:
            doc.save()
        return doc


class DocumentChunkInline(admin.TabularInline):
    model = DocumentChunk
    extra = 0
    can_delete = False
    max_num = 0
    fields = ("chunk_index", "token_count", "content_preview", "metadata", "created_at")
    readonly_fields = ("chunk_index", "token_count", "content_preview", "metadata", "created_at")
    ordering = ("chunk_index",)

    @admin.display(description="Content")
    def content_preview(self, obj: DocumentChunk) -> str:
        text = obj.content or ""
        return text[:160] + ("…" if len(text) > 160 else "")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "short_id",
        "tenant",
        "topic",
        "source_type",
        "source_uri_short",
        "status_badge",
        "semantic_score",
        "chunk_count",
        "created_at",
    )
    list_filter = ("status", "source_type", "tenant", "topic")
    search_fields = ("id", "source_uri", "content_hash", "topic__name", "tenant__name")
    readonly_fields = (
        "id",
        "tenant",
        "topic",
        "source_type",
        "source_uri",
        "raw_storage_key",
        "normalized_storage_key",
        "content_hash",
        "status",
        "schema_version_applied",
        "semantic_score",
        "extracted_metadata",
        "validation_report",
        "created_at",
        "updated_at",
    )
    actions = ["reindex_selected", "retry_selected"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Document]:
        return (
            super()
            .get_queryset(request)
            .select_related("tenant", "topic")
            .annotate(_chunk_count=Count("chunks"))
        )

    def get_readonly_fields(self, request: HttpRequest, obj: Document | None = None) -> tuple[str, ...]:
        if obj is None:
            return ()
        return self.readonly_fields

    def get_form(self, request: HttpRequest, obj: Document | None = None, **kwargs: Any) -> Any:
        if obj is None:
            kwargs["form"] = DocumentCreateAdminForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request: HttpRequest, obj: Document | None = None) -> list[tuple[str | None, dict[str, Any]]]:
        if obj is None:
            return [
                (
                    "Ingest Document",
                    {
                        "fields": (
                            "topic",
                            "source_type",
                            "upload_file",
                            "source_url",
                            "force_reindex",
                            "run_sync",
                        )
                    },
                )
            ]
        return [
            (
                "Document Overview",
                {
                    "fields": (
                        "id",
                        "tenant",
                        "topic",
                        "source_type",
                        "source_uri",
                        "status",
                        "semantic_score",
                        "schema_version_applied",
                    )
                },
            ),
            (
                "Extracted Metadata & Validation",
                {
                    "fields": (
                        "extracted_metadata",
                        "validation_report",
                    )
                },
            ),
            (
                "Storage & Timestamps",
                {
                    "classes": ("collapse",),
                    "fields": (
                        "content_hash",
                        "raw_storage_key",
                        "normalized_storage_key",
                        "created_at",
                        "updated_at",
                    ),
                },
            ),
        ]

    def get_inlines(self, request: HttpRequest, obj: Document | None = None) -> list[type[admin.TabularInline]]:
        return [DocumentChunkInline] if obj is not None else []

    def save_model(self, request: HttpRequest, obj: Document, form: forms.ModelForm, change: bool) -> None:
        super().save_model(request, obj, form, change)
        if not change:
            run_sync = bool(form.cleaned_data.get("run_sync"))
            if run_sync:
                try:
                    run_ingestion_pipeline_sync(str(obj.id))
                    obj.refresh_from_db()
                    self.message_user(
                        request,
                        f"Synchronous ingestion completed for {obj.id} — final status: {obj.status}.",
                        level=messages.SUCCESS if obj.status == Document.Status.INDEXED else messages.WARNING,
                    )
                except Exception as exc:
                    self.message_user(
                        request,
                        f"Synchronous ingestion failed for {obj.id}: {exc}",
                        level=messages.ERROR,
                    )
            else:
                dispatch_ingestion_pipeline(str(obj.id))
                self.message_user(
                    request,
                    f"Queued ingestion pipeline for {obj.id} on Celery queues (parsing -> llm_io -> indexing).",
                    level=messages.INFO,
                )

    def delete_model(self, request: HttpRequest, obj: Document) -> None:
        raw_key, norm_key = obj.raw_storage_key, obj.normalized_storage_key
        super().delete_model(request, obj)
        delete_key(raw_key)
        delete_key(norm_key)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Document]) -> None:
        keys = [(d.raw_storage_key, d.normalized_storage_key) for d in queryset]
        super().delete_queryset(request, queryset)
        for raw_key, norm_key in keys:
            delete_key(raw_key)
            delete_key(norm_key)

    @admin.display(description="ID")
    def short_id(self, obj: Document) -> str:
        return str(obj.id)[:8]

    @admin.display(description="Source")
    def source_uri_short(self, obj: Document) -> str:
        uri = obj.source_uri or ""
        return uri[:60] + ("…" if len(uri) > 60 else "")

    @admin.display(description="Status")
    def status_badge(self, obj: Document) -> str:
        color = STATUS_COLORS.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">{}</span>',
            color,
            obj.status,
        )

    @admin.display(description="Chunks", ordering="_chunk_count")
    def chunk_count(self, obj: Document) -> int:
        return int(getattr(obj, "_chunk_count", 0))

    @admin.action(description="Re-index selected documents (Celery)")
    def reindex_selected(self, request: HttpRequest, queryset: QuerySet[Document]) -> None:
        dispatched = 0
        for doc in queryset:
            if doc.normalized_storage_key:
                dispatch_index_only(str(doc.id))
                dispatched += 1
            elif doc.raw_storage_key or doc.source_type == Document.SourceType.URL:
                dispatch_ingestion_pipeline(str(doc.id))
                dispatched += 1
        self.message_user(request, f"Dispatched re-indexing for {dispatched} document(s).", level=messages.INFO)

    @admin.action(description="Retry full ingestion pipeline for selected documents (Celery)")
    def retry_selected(self, request: HttpRequest, queryset: QuerySet[Document]) -> None:
        count = 0
        for doc in queryset:
            doc.status = Document.Status.PENDING
            doc.save(update_fields=["status", "updated_at"])
            dispatch_ingestion_pipeline(str(doc.id))
            count += 1
        self.message_user(request, f"Queued full ingestion retry for {count} document(s).", level=messages.INFO)


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "tenant", "topic", "chunk_index", "token_count", "content_preview", "created_at")
    list_filter = ("tenant", "topic")
    search_fields = ("content", "document__source_uri", "document__id")
    readonly_fields = (
        "id",
        "tenant",
        "topic",
        "document",
        "chunk_index",
        "token_count",
        "content",
        "metadata",
        "created_at",
    )
    exclude = ("embedding", "search_vector")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: DocumentChunk | None = None) -> bool:
        return False

    @admin.display(description="Content")
    def content_preview(self, obj: DocumentChunk) -> str:
        text = obj.content or ""
        return text[:120] + ("…" if len(text) > 120 else "")
