from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.ingestion.models import Document, DocumentChunk
from apps.ingestion.parsers import SSRFBlockedError, validate_url_ssrf
from apps.llm_gateway.models import LLMUsageLog
from apps.quarantine.models import QuarantineReview
from apps.topics.models import Topic


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.10/secret",
        "file:///etc/passwd",
        "ftp://example.com/file.txt",
    ],
)
def test_ssrf_blocks_disallowed_urls(blocked_url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        validate_url_ssrf(blocked_url)


@pytest.mark.django_db
def test_file_ingestion_dedupe_force_reindex_and_delete(
    client: Client,
    topic_a: Topic,
    auth_headers_a: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    body = b"<!-- page: 1 -->\n# Access Control\nAll SOC2 systems require MFA.\n\n## Encryption\nTLS 1.3 everywhere."
    f1 = SimpleUploadedFile("sec.md", body, content_type="text/markdown")

    resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f1},
        **auth_headers_a,
    )
    assert resp.status_code == 202
    doc_id = resp.json()["document_id"]

    status_resp = client.get(f"/api/v1/documents/{doc_id}", **auth_headers_a)
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == Document.Status.INDEXED
    assert data["extracted_metadata"] == {"department": "security", "year": 2026}
    assert data["semantic_score"] == pytest.approx(0.92)

    chunks = list(DocumentChunk.objects.filter(document_id=doc_id).order_by("chunk_index"))
    assert len(chunks) >= 2
    assert chunks[0].metadata["department"] == "security"
    assert chunks[0].metadata["page"] == 1
    assert chunks[0].search_vector is not None
    assert LLMUsageLog.objects.filter(tenant=topic_a.tenant).count() >= 3

    # Duplicate upload returns 200 OK with deduplicated=True
    f2 = SimpleUploadedFile("sec.md", body, content_type="text/markdown")
    dup = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f2},
        **auth_headers_a,
    )
    assert dup.status_code == 200
    assert dup.json()["document_id"] == doc_id
    assert dup.json()["deduplicated"] is True

    # force_reindex=true triggers new ingestion (202)
    f3 = SimpleUploadedFile("sec.md", body, content_type="text/markdown")
    forced = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f3, "force_reindex": "true"},
        **auth_headers_a,
    )
    assert forced.status_code == 202
    assert forced.json()["document_id"] != doc_id

    # Delete removes document & chunks
    del_resp = client.delete(f"/api/v1/documents/{doc_id}", **auth_headers_a)
    assert del_resp.status_code == 204
    assert not Document.objects.filter(id=doc_id).exists()
    assert not DocumentChunk.objects.filter(document_id=doc_id).exists()


@pytest.mark.django_db
def test_strict_reject_vs_quarantine_with_suggested_topics(
    client: Client,
    topic_a: Topic,
    topic_a_hr: Topic,
    auth_headers_a: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    mock_litellm["gate_response"] = {
        "is_on_topic": False,
        "confidence_score": 0.25,
        "rationale": "This document is about PTO, not security.",
    }
    mock_litellm["suggestion_response"] = {
        "suggested_topics": [
            {
                "topic_id": str(topic_a_hr.id),
                "slug": topic_a_hr.slug,
                "confidence": 0.91,
                "reason": "Matches HR PTO handbook.",
            }
        ]
    }

    # 1. QUARANTINE flow
    f1 = SimpleUploadedFile("pto.md", b"# PTO Policy\nEmployees get 20 days PTO.", content_type="text/markdown")
    resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f1},
        **auth_headers_a,
    )
    assert resp.status_code == 202
    q_doc_id = resp.json()["document_id"]
    doc = Document.objects.get(id=q_doc_id)
    assert doc.status == Document.Status.QUARANTINED
    assert DocumentChunk.objects.filter(document=doc).count() == 0

    review = QuarantineReview.objects.get(document=doc)
    assert review.failure_type == QuarantineReview.FailureType.SEMANTIC_MISMATCH
    assert len(review.suggested_topics) == 1
    assert review.suggested_topics[0]["slug"] == topic_a_hr.slug

    # 2. STRICT_REJECT flow
    topic_a.failure_policy = Topic.FailurePolicy.STRICT_REJECT
    topic_a.save()

    f2 = SimpleUploadedFile("cake.md", b"# Cake Recipe\nFlour and sugar.", content_type="text/markdown")
    resp2 = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f2},
        **auth_headers_a,
    )
    assert resp2.status_code == 202
    r_doc_id = resp2.json()["document_id"]
    r_doc = Document.objects.get(id=r_doc_id)
    assert r_doc.status == Document.Status.REJECTED
    assert not QuarantineReview.objects.filter(document=r_doc).exists()
    assert DocumentChunk.objects.filter(document=r_doc).count() == 0


@pytest.mark.django_db
def test_url_ingestion_end_to_end(
    client: Client,
    topic_a: Topic,
    auth_headers_a: dict[str, str],
    mock_litellm: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "apps.ingestion.tasks.parse_url_to_markdown",
        lambda url: "# Compliance Guide\nSOC2 audit logging requirements.",
    )
    resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/url",
        data=json.dumps({"url": "https://example.com/soc2", "force_reindex": False}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert resp.status_code == 202
    doc = Document.objects.get(id=resp.json()["document_id"])
    assert doc.status == Document.Status.INDEXED
    assert DocumentChunk.objects.filter(document=doc).count() == 1
