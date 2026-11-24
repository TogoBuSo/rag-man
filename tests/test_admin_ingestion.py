from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.ingestion.models import Document, DocumentChunk
from apps.quarantine.models import QuarantineReview
from apps.topics.models import Topic


@pytest.fixture
def admin_client(db: Any) -> Client:
    User = get_user_model()
    user = User.objects.create_superuser(username="admin_test", email="admin@example.com", password="pw")
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_admin_ingestion_views_render(admin_client: Client) -> None:
    for url in (
        "/admin/ingestion/document/",
        "/admin/ingestion/document/add/",
        "/admin/ingestion/documentchunk/",
        "/admin/quarantine/quarantinereview/",
        "/admin/llm_gateway/llmusagelog/",
    ):
        resp = admin_client.get(url)
        assert resp.status_code == 200, f"Expected 200 for {url}, got {resp.status_code}"


@pytest.mark.django_db
def test_admin_document_upload_sync_and_async_and_quarantine_resolution(
    admin_client: Client,
    topic_a: Topic,
    mock_litellm: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Upload file with run_sync=True -> immediately indexed
    f1 = SimpleUploadedFile("admin_doc.md", b"# Security\nAdmin sync upload test.", content_type="text/markdown")
    resp = admin_client.post(
        "/admin/ingestion/document/add/",
        data={
            "topic": str(topic_a.id),
            "source_type": Document.SourceType.FILE,
            "upload_file": f1,
            "run_sync": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    doc = Document.objects.get(source_uri="admin_doc.md")
    assert doc.status == Document.Status.INDEXED
    assert DocumentChunk.objects.filter(document=doc).count() == 1

    # Inspecting the document change view renders inline chunks cleanly
    assert admin_client.get(f"/admin/ingestion/document/{doc.id}/change/").status_code == 200

    # 2. Upload file with run_sync=False -> dispatches async Celery pipeline
    dispatched: list[str] = []
    monkeypatch.setattr("apps.ingestion.admin.dispatch_ingestion_pipeline", lambda doc_id: dispatched.append(doc_id))
    f2 = SimpleUploadedFile("async_doc.md", b"# Security\nAsync dispatch test.", content_type="text/markdown")
    resp2 = admin_client.post(
        "/admin/ingestion/document/add/",
        data={
            "topic": str(topic_a.id),
            "source_type": Document.SourceType.FILE,
            "upload_file": f2,
        },
        follow=True,
    )
    assert resp2.status_code == 200
    assert len(dispatched) == 1

    # 3. Quarantine review resolution via Admin change page
    mock_litellm["gate_response"] = {"is_on_topic": True, "confidence_score": 0.9, "rationale": "On topic"}
    mock_litellm["extraction_response"] = {"department": "security"}  # missing required 'year'
    f3 = SimpleUploadedFile("quarantine_admin.md", b"# Security\nNeeds metadata edit.", content_type="text/markdown")
    admin_client.post(
        "/admin/ingestion/document/add/",
        data={
            "topic": str(topic_a.id),
            "source_type": Document.SourceType.FILE,
            "upload_file": f3,
            "run_sync": "on",
        },
        follow=True,
    )
    q_doc = Document.objects.get(source_uri="quarantine_admin.md")
    assert q_doc.status == Document.Status.QUARANTINED
    review = QuarantineReview.objects.get(document=q_doc)

    resolve_resp = admin_client.post(
        f"/admin/quarantine/quarantinereview/{review.id}/change/",
        data={
            "resolve_action": QuarantineReview.Resolution.EDITED_AND_INDEXED,
            "edited_metadata_json": '{"department": "security", "year": 2026}',
            "resolution_notes": "Fixed in Django Admin",
        },
        follow=True,
    )
    assert resolve_resp.status_code == 200
    review.refresh_from_db()
    q_doc.refresh_from_db()
    assert review.resolution == QuarantineReview.Resolution.EDITED_AND_INDEXED
    assert q_doc.status == Document.Status.INDEXED
