from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.ingestion.models import Document
from apps.quarantine.models import QuarantineReview
from apps.tenancy.models import Tenant, TenantAPIKey, TenantProviderConfig
from apps.topics.models import Topic


@pytest.mark.django_db
def test_auth_rejects_missing_invalid_and_expired_keys(client: Client, tenant_a: tuple[Tenant, str]) -> None:
    assert client.get("/api/v1/topics").status_code == 401
    assert client.get("/api/v1/topics", HTTP_AUTHORIZATION="Bearer invalid-key").status_code == 401

    expired_obj, expired_raw = TenantAPIKey.create_key(
        tenant=tenant_a[0],
        name="expired",
        expires_at=timezone.now() - timedelta(minutes=5),
    )
    assert client.get("/api/v1/topics", HTTP_AUTHORIZATION=f"Bearer {expired_raw}").status_code == 401

    expired_obj.expires_at = None
    expired_obj.is_active = False
    expired_obj.save()
    assert client.get("/api/v1/topics", HTTP_X_API_KEY=expired_raw).status_code == 401


@pytest.mark.django_db
def test_zero_cross_tenant_leakage_across_all_endpoints(
    client: Client,
    tenant_a: tuple[Tenant, str],
    tenant_b: tuple[Tenant, str],
    topic_a: Topic,
    auth_headers_a: dict[str, str],
    auth_headers_b: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    # Tenant A uploads an indexed document
    f1 = SimpleUploadedFile("policy.md", b"# Security Policy\nSOC2 compliance controls.", content_type="text/markdown")
    resp = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f1},
        **auth_headers_a,
    )
    assert resp.status_code == 202
    doc_id = resp.json()["document_id"]

    # Tenant A uploads a quarantined document
    mock_litellm["gate_response"] = {"is_on_topic": False, "confidence_score": 0.1, "rationale": "Off topic"}
    f2 = SimpleUploadedFile("pasta.md", b"# Pasta Recipe\nBoil water.", content_type="text/markdown")
    resp_q = client.post(
        f"/api/v1/topics/{topic_a.id}/documents/upload",
        data={"file": f2},
        **auth_headers_a,
    )
    assert resp_q.status_code == 202
    q_doc_id = resp_q.json()["document_id"]
    review = QuarantineReview.objects.get(document_id=q_doc_id)

    # Tenant B cannot list or patch Tenant A's topics
    assert client.get("/api/v1/topics", **auth_headers_b).json() == []
    assert (
        client.patch(
            f"/api/v1/topics/{topic_a.id}",
            data=json.dumps({"name": "Hijacked"}),
            content_type="application/json",
            **auth_headers_b,
        ).status_code
        == 404
    )

    # Tenant B cannot upload to, view, or delete Tenant A's documents
    f3 = SimpleUploadedFile("intrude.md", b"hello", content_type="text/markdown")
    assert (
        client.post(
            f"/api/v1/topics/{topic_a.id}/documents/upload",
            data={"file": f3},
            **auth_headers_b,
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/documents/{doc_id}", **auth_headers_b).status_code == 404
    assert client.delete(f"/api/v1/documents/{doc_id}", **auth_headers_b).status_code == 404
    assert Document.objects.filter(id=doc_id).exists()

    # Tenant B cannot see or resolve Tenant A's quarantine reviews
    assert client.get("/api/v1/quarantine", **auth_headers_b).json() == []
    assert (
        client.post(
            f"/api/v1/quarantine/{review.id}/resolve",
            data=json.dumps({"action": "REJECTED"}),
            content_type="application/json",
            **auth_headers_b,
        ).status_code
        == 404
    )

    # Tenant B cannot retrieve or synthesize against Tenant A's topic
    rag_payload = json.dumps({"query": "SOC2", "topic_ids": [str(topic_a.id)]})
    assert (
        client.post("/api/v1/rag/retrieve", data=rag_payload, content_type="application/json", **auth_headers_b).status_code
        == 404
    )
    assert (
        client.post("/api/v1/rag/synthesize", data=rag_payload, content_type="application/json", **auth_headers_b).status_code
        == 404
    )


@pytest.mark.django_db
def test_provider_config_encrypted_api_keys(tenant_a: tuple[Tenant, str]) -> None:
    cfg = TenantProviderConfig.objects.create(tenant=tenant_a[0])
    cfg.set_api_keys({"openai": "sk-secret-byok-123"})
    cfg.save()

    reloaded = TenantProviderConfig.objects.get(tenant=tenant_a[0])
    assert reloaded.get_api_keys() == {"openai": "sk-secret-byok-123"}
