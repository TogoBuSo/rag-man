from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.ingestion.models import Document, DocumentChunk
from apps.quarantine.models import QuarantineReview
from apps.topics.models import Topic


@pytest.mark.django_db
def test_api_manual_text_with_child_attachments_and_cascading_delete(
    client: Client,
    topic_a: Topic,
    auth_headers_a: dict[str, str],
    auth_headers_b: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    att1 = SimpleUploadedFile("evidence_a.md", b"# Evidence A\nFirewall logs for SOC2.", content_type="text/markdown")
    att2 = SimpleUploadedFile("evidence_b.txt", b"Encryption audit report 2026.", content_type="text/plain")

    # Cross-tenant isolation: Tenant B cannot submit to Tenant A's topic
    cross = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/text",
        data={"title": "Intrusion", "content": "Unauthorized text"},
        **auth_headers_b,
    )
    assert cross.status_code == 404

    # Tenant A submits manual text + 2 attachments
    resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/text",
        data={
            "title": "Q1 Security Review Notes",
            "content": "Manual summary of SOC2 access control review.",
            "attachments": [att1, att2],
        },
        **auth_headers_a,
    )
    assert resp.status_code == 202
    payload = resp.json()
    parent_id = payload["document_id"]
    assert payload["deduplicated"] is False
    assert len(payload["attachments"]) == 2

    child_ids = [a["document_id"] for a in payload["attachments"]]
    parent_doc = Document.objects.get(id=parent_id)
    assert parent_doc.source_type == Document.SourceType.TEXT
    assert parent_doc.source_uri == "Q1 Security Review Notes"
    assert parent_doc.status == Document.Status.INDEXED

    children = list(Document.objects.filter(parent_document=parent_doc).order_by("created_at"))
    assert len(children) == 2
    assert {str(c.id) for c in children} == set(child_ids)
    assert all(c.source_type == Document.SourceType.FILE and c.status == Document.Status.INDEXED for c in children)

    # GET /documents/{parent_id} exposes child_documents summary
    doc_resp = client.get(f"/api/v1/documents/{parent_id}", **auth_headers_a)
    assert doc_resp.status_code == 200
    doc_data = doc_resp.json()
    assert doc_data["parent_document_id"] is None
    assert len(doc_data["child_documents"]) == 2

    # GET /documents/{child_id} exposes parent_document_id
    child_resp = client.get(f"/api/v1/documents/{child_ids[0]}", **auth_headers_a)
    assert child_resp.status_code == 200
    assert child_resp.json()["parent_document_id"] == parent_id

    # Both parent manual text and child attachment chunks are retrievable
    ret = client.post(
        "/api/v1/rag/retrieve",
        data=json.dumps({
            "query": "Firewall logs SOC2 access control",
            "topic_ids": [str(topic_a.id)],
            "top_k": 5,
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert ret.status_code == 200
    retrieved_doc_ids = {c["document_id"] for c in ret.json()}
    assert parent_id in retrieved_doc_ids
    assert child_ids[0] in retrieved_doc_ids

    # Re-submitting identical text + identical attachments returns 200 OK (all deduplicated)
    att1_dup = SimpleUploadedFile("evidence_a.md", b"# Evidence A\nFirewall logs for SOC2.", content_type="text/markdown")
    att2_dup = SimpleUploadedFile("evidence_b.txt", b"Encryption audit report 2026.", content_type="text/plain")
    dup_resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/text",
        data={
            "title": "Q1 Security Review Notes",
            "content": "Manual summary of SOC2 access control review.",
            "attachments": [att1_dup, att2_dup],
        },
        **auth_headers_a,
    )
    assert dup_resp.status_code == 200
    assert dup_resp.json()["deduplicated"] is True
    assert all(a["deduplicated"] is True for a in dup_resp.json()["attachments"])

    # Deleting parent cascades to child documents and their chunks
    del_resp = client.delete(f"/api/v1/documents/{parent_id}", **auth_headers_a)
    assert del_resp.status_code == 204
    assert not Document.objects.filter(id=parent_id).exists()
    assert not Document.objects.filter(id__in=child_ids).exists()
    assert DocumentChunk.objects.filter(document_id__in=[parent_id, *child_ids]).count() == 0


@pytest.mark.django_db
def test_manual_text_independent_child_quarantine(
    client: Client,
    topic_a: Topic,
    auth_headers_a: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mock_litellm: dict[str, Any],
) -> None:
    """Parent manual text can index while an off-topic child attachment is independently quarantined."""
    from apps.llm_gateway.client import llm_gateway

    original_complete_json = llm_gateway.complete_json

    def selective_gate(**kwargs: Any) -> dict[str, Any]:
        messages = kwargs.get("messages", [])
        if messages and "semantic gatekeeper" in messages[0]["content"].lower():
            if "Banana bread" in messages[1]["content"]:
                return {"is_on_topic": False, "confidence_score": 0.1, "rationale": "Off-topic recipe"}
            return {"is_on_topic": True, "confidence_score": 0.95, "rationale": "On-topic security"}
        return original_complete_json(**kwargs)

    monkeypatch.setattr(llm_gateway, "complete_json", selective_gate)

    off_topic = SimpleUploadedFile("recipe.md", b"# Recipe\nBanana bread ingredients.", content_type="text/markdown")
    resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/text",
        data={
            "title": "Security Audit Note",
            "content": "All SOC2 controls passed.",
            "attachments": [off_topic],
        },
        **auth_headers_a,
    )
    assert resp.status_code == 202
    data = resp.json()
    parent = Document.objects.get(id=data["document_id"])
    child = Document.objects.get(id=data["attachments"][0]["document_id"])
    assert parent.status == Document.Status.INDEXED
    assert child.status == Document.Status.QUARANTINED
    assert QuarantineReview.objects.filter(document=child).exists()
    assert not QuarantineReview.objects.filter(document=parent).exists()


@pytest.mark.django_db
def test_admin_manual_text_submission_with_multiple_attachments(
    topic_a: Topic,
    mock_litellm: dict[str, Any],
) -> None:
    User = get_user_model()
    user = User.objects.create_superuser(username="admin_text", email="admin@example.com", password="pw")
    admin_client = Client()
    admin_client.force_login(user)

    att1 = SimpleUploadedFile("att1.md", b"# Attachment 1\nSOC2 policy detail.", content_type="text/markdown")
    att2 = SimpleUploadedFile("att2.md", b"# Attachment 2\nEncryption standard.", content_type="text/markdown")

    resp = admin_client.post(
        "/admin/ingestion/document/add/",
        data={
            "topic": str(topic_a.id),
            "source_type": Document.SourceType.TEXT,
            "title": "Admin Manual Note",
            "manual_text": "Manual security note submitted via Django Admin.",
            "attachments": [att1, att2],
            "run_sync": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    parent = Document.objects.get(source_uri="Admin Manual Note", source_type=Document.SourceType.TEXT)
    assert parent.status == Document.Status.INDEXED
    children = list(Document.objects.filter(parent_document=parent))
    assert len(children) == 2
    assert all(c.status == Document.Status.INDEXED for c in children)

    # Parent change view renders ChildDocumentInline cleanly
    assert admin_client.get(f"/admin/ingestion/document/{parent.id}/change/").status_code == 200
    assert admin_client.get(f"/admin/ingestion/document/{children[0].id}/change/").status_code == 200
