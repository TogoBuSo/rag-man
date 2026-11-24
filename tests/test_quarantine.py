from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.ingestion.models import Document, DocumentChunk
from apps.quarantine.models import QuarantineReview
from apps.topics.models import Topic


def _create_quarantined_doc(
    client: Client,
    topic: Topic,
    auth_headers: dict[str, str],
    filename: str,
    content: bytes,
) -> QuarantineReview:
    f = SimpleUploadedFile(filename, content, content_type="text/markdown")
    resp = client.post(f"/api/v1/topics/{topic.id}/documents/upload", data={"file": f}, **auth_headers)
    assert resp.status_code == 202
    return QuarantineReview.objects.get(document_id=resp.json()["document_id"])


@pytest.mark.django_db
def test_quarantine_all_resolution_paths(
    client: Client,
    topic_a: Topic,
    topic_a_hr: Topic,
    auth_headers_a: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    # Force schema validation failure (missing required 'year')
    mock_litellm["gate_response"] = {"is_on_topic": True, "confidence_score": 0.9, "rationale": "On topic"}
    mock_litellm["extraction_response"] = {"department": "security"}

    # 1. EDITED_AND_INDEXED (rejects invalid metadata with 422, accepts valid metadata)
    rev_edit = _create_quarantined_doc(client, topic_a, auth_headers_a, "edit.md", b"# Firewall Rules\nDeny all.")
    assert rev_edit.failure_type == QuarantineReview.FailureType.SCHEMA_VALIDATION

    bad_resp = client.post(
        f"/api/v1/quarantine/{rev_edit.id}/resolve",
        data=json.dumps({"action": "EDITED_AND_INDEXED", "edited_metadata": {"department": "security"}}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert bad_resp.status_code == 422

    ok_resp = client.post(
        f"/api/v1/quarantine/{rev_edit.id}/resolve",
        data=json.dumps({
            "action": "EDITED_AND_INDEXED",
            "edited_metadata": {"department": "security", "year": 2026},
            "resolution_notes": "Added missing year",
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert ok_resp.status_code == 200
    assert ok_resp.json()["resolution"] == QuarantineReview.Resolution.EDITED_AND_INDEXED
    rev_edit.document.refresh_from_db()
    assert rev_edit.document.status == Document.Status.INDEXED
    assert DocumentChunk.objects.filter(document=rev_edit.document).count() == 1

    # Re-resolving returns 409
    again = client.post(
        f"/api/v1/quarantine/{rev_edit.id}/resolve",
        data=json.dumps({"action": "REJECTED"}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert again.status_code == 409

    # 2. APPROVED_OVERRIDE
    rev_override = _create_quarantined_doc(client, topic_a, auth_headers_a, "ovr.md", b"# Legacy Policy\nOverride me.")
    resp_ovr = client.post(
        f"/api/v1/quarantine/{rev_override.id}/resolve",
        data=json.dumps({"action": "APPROVED_OVERRIDE", "resolution_notes": "Approved by CISO"}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert resp_ovr.status_code == 200
    rev_override.document.refresh_from_db()
    assert rev_override.document.status == Document.Status.INDEXED

    # 3. REASSIGNED -> re-validates under HR topic and indexes
    rev_reassign = _create_quarantined_doc(client, topic_a, auth_headers_a, "hr.md", b"# PTO Guide\n20 days PTO.")
    mock_litellm["extraction_response"] = {"department": "hr", "year": 2026}
    resp_re = client.post(
        f"/api/v1/quarantine/{rev_reassign.id}/resolve",
        data=json.dumps({"action": "REASSIGNED", "target_topic_id": str(topic_a_hr.id)}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert resp_re.status_code == 200
    rev_reassign.document.refresh_from_db()
    assert rev_reassign.document.topic_id == topic_a_hr.id
    assert rev_reassign.document.status == Document.Status.INDEXED

    # 4. REJECTED
    mock_litellm["extraction_response"] = {"department": "security"}
    rev_reject = _create_quarantined_doc(client, topic_a, auth_headers_a, "rej.md", b"# Spam\nIgnore.")
    assert len(client.get("/api/v1/quarantine", **auth_headers_a).json()) == 1

    resp_rej = client.post(
        f"/api/v1/quarantine/{rev_reject.id}/resolve",
        data=json.dumps({"action": "REJECTED", "resolution_notes": "Not relevant"}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert resp_rej.status_code == 200
    rev_reject.document.refresh_from_db()
    assert rev_reject.document.status == Document.Status.REJECTED
    assert client.get("/api/v1/quarantine", **auth_headers_a).json() == []
