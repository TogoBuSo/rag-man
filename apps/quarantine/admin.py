from __future__ import annotations

import json
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.html import format_html
from ninja.errors import HttpError

from apps.quarantine.models import QuarantineReview
from apps.quarantine.schemas import QuarantineResolveIn
from apps.quarantine.services import resolve_quarantine_review
from apps.topics.models import Topic

RESOLUTION_COLORS: dict[str, str] = {
    QuarantineReview.Resolution.PENDING: "#fd7e14",
    QuarantineReview.Resolution.APPROVED_OVERRIDE: "#198754",
    QuarantineReview.Resolution.EDITED_AND_INDEXED: "#0d6efd",
    QuarantineReview.Resolution.REASSIGNED: "#6610f2",
    QuarantineReview.Resolution.REJECTED: "#dc3545",
}


class QuarantineReviewAdminForm(forms.ModelForm):
    resolve_action = forms.ChoiceField(
        required=False,
        label="Resolution Action",
        choices=[
            ("", "— No action (leave as-is) —"),
            (QuarantineReview.Resolution.APPROVED_OVERRIDE, "Approve Override & Index"),
            (QuarantineReview.Resolution.EDITED_AND_INDEXED, "Edit Metadata & Index"),
            (QuarantineReview.Resolution.REASSIGNED, "Reassign to Another Topic"),
            (QuarantineReview.Resolution.REJECTED, "Reject Document"),
        ],
        help_text="Select an action to resolve a PENDING review.",
    )
    edited_metadata_json = forms.JSONField(
        required=False,
        label="Edited Metadata (JSON)",
        help_text="Required for 'Edit Metadata & Index'. Must validate against the Topic's JSON Schema.",
    )
    target_topic = forms.ModelChoiceField(
        required=False,
        queryset=Topic.objects.none(),
        label="Target Topic (for Reassign)",
        help_text="Required for 'Reassign to Another Topic'. Limited to active Topics of the same tenant.",
    )

    class Meta:
        model = QuarantineReview
        fields = ["resolution_notes"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["edited_metadata_json"].initial = self.instance.proposed_metadata or {}
            self.fields["target_topic"].queryset = (
                Topic.objects.for_tenant(self.instance.tenant)
                .filter(is_active=True)
                .order_by("name")
            )
            if self.instance.resolution != QuarantineReview.Resolution.PENDING:
                self.fields["resolve_action"].disabled = True
                self.fields["edited_metadata_json"].disabled = True
                self.fields["target_topic"].disabled = True

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        action = cleaned.get("resolve_action")
        if not action:
            return cleaned

        if self.instance.resolution != QuarantineReview.Resolution.PENDING:
            raise forms.ValidationError("This quarantine review has already been resolved.")
        if action == QuarantineReview.Resolution.EDITED_AND_INDEXED and cleaned.get("edited_metadata_json") is None:
            self.add_error("edited_metadata_json", "Provide valid JSON metadata for 'Edit Metadata & Index'.")
        if action == QuarantineReview.Resolution.REASSIGNED and cleaned.get("target_topic") is None:
            self.add_error("target_topic", "Select a target topic for 'Reassign to Another Topic'.")
        return cleaned


@admin.register(QuarantineReview)
class QuarantineReviewAdmin(admin.ModelAdmin):
    form = QuarantineReviewAdminForm
    list_display = (
        "document",
        "tenant",
        "failure_type",
        "resolution_badge",
        "suggested_topic_slugs",
        "created_at",
        "resolved_at",
    )
    list_filter = ("resolution", "failure_type", "tenant")
    search_fields = ("document__source_uri", "document__id", "tenant__name")
    readonly_fields = (
        "id",
        "tenant",
        "document",
        "failure_type",
        "suggested_topics",
        "proposed_metadata",
        "document_validation_report",
        "resolution",
        "resolved_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Quarantine Details",
            {
                "fields": (
                    "id",
                    "tenant",
                    "document",
                    "failure_type",
                    "resolution",
                    "suggested_topics",
                    "proposed_metadata",
                    "document_validation_report",
                )
            },
        ),
        (
            "Resolve Review (HITL)",
            {
                "fields": (
                    "resolve_action",
                    "edited_metadata_json",
                    "target_topic",
                    "resolution_notes",
                )
            },
        ),
        (
            "Timestamps",
            {
                "classes": ("collapse",),
                "fields": ("resolved_at", "created_at", "updated_at"),
            },
        ),
    )
    actions = ["approve_override_selected", "reject_selected"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def save_model(self, request: HttpRequest, obj: QuarantineReview, form: forms.ModelForm, change: bool) -> None:
        action = form.cleaned_data.get("resolve_action")
        if action:
            target_topic: Topic | None = form.cleaned_data.get("target_topic")
            payload = QuarantineResolveIn(
                action=action,
                edited_metadata=form.cleaned_data.get("edited_metadata_json"),
                target_topic_id=target_topic.id if target_topic else None,
                resolution_notes=form.cleaned_data.get("resolution_notes", ""),
            )
            try:
                resolve_quarantine_review(obj.tenant, obj.id, payload)
                self.message_user(
                    request,
                    f"Quarantine review {obj.id} resolved with action '{action}'.",
                    level=messages.SUCCESS,
                )
            except HttpError as exc:
                self.message_user(request, f"Resolution failed: {exc.message}", level=messages.ERROR)
        else:
            super().save_model(request, obj, form, change)

    @admin.display(description="Resolution")
    def resolution_badge(self, obj: QuarantineReview) -> str:
        color = RESOLUTION_COLORS.get(obj.resolution, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">{}</span>',
            color,
            obj.resolution,
        )

    @admin.display(description="Suggested Topics")
    def suggested_topic_slugs(self, obj: QuarantineReview) -> str:
        if not obj.suggested_topics:
            return "—"
        slugs = [str(item.get("slug", item.get("topic_id", ""))) for item in obj.suggested_topics if isinstance(item, dict)]
        return ", ".join(s for s in slugs if s) or "—"

    @admin.display(description="Document Validation Report")
    def document_validation_report(self, obj: QuarantineReview) -> str:
        return json.dumps(obj.document.validation_report, indent=2)

    @admin.action(description="Approve Override & Index selected PENDING reviews")
    def approve_override_selected(self, request: HttpRequest, queryset: QuerySet[QuarantineReview]) -> None:
        resolved = 0
        for review in queryset.filter(resolution=QuarantineReview.Resolution.PENDING):
            resolve_quarantine_review(
                review.tenant,
                review.id,
                QuarantineResolveIn(action="APPROVED_OVERRIDE", resolution_notes="Bulk approved in Admin"),
            )
            resolved += 1
        self.message_user(request, f"Approved override & queued indexing for {resolved} review(s).", level=messages.SUCCESS)

    @admin.action(description="Reject selected PENDING reviews")
    def reject_selected(self, request: HttpRequest, queryset: QuerySet[QuarantineReview]) -> None:
        resolved = 0
        for review in queryset.filter(resolution=QuarantineReview.Resolution.PENDING):
            resolve_quarantine_review(
                review.tenant,
                review.id,
                QuarantineResolveIn(action="REJECTED", resolution_notes="Bulk rejected in Admin"),
            )
            resolved += 1
        self.message_user(request, f"Rejected {resolved} review(s).", level=messages.WARNING)
