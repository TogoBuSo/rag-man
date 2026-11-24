from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client

from apps.ingestion.models import DocumentChunk
from apps.llm_gateway.models import LLMUsageLog
from apps.tenancy.models import Tenant
from apps.topics.models import Topic


@pytest.mark.django_db
def test_create_tenant_command_and_api_auth(client: Client) -> None:
    out = StringIO()
    call_command("create_tenant", "--name", "Gamma Inc", "--slug", "gamma", stdout=out)
    output = out.getvalue()
    assert "Created tenant 'Gamma Inc'" in output
    raw_key = output.strip().split(": ")[-1].strip()

    assert Tenant.objects.filter(slug="gamma").exists()
    resp = client.get("/api/v1/topics", HTTP_AUTHORIZATION=f"Bearer {raw_key}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.django_db
def test_reindex_embeddings_command(
    client: Client,
    topic_a: Topic,
    auth_headers_a: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    f = SimpleUploadedFile("doc.md", b"# Security\nMFA is mandatory.", content_type="text/markdown")
    assert client.post(f"/api/v1/topics/{topic_a.id}/documents/upload", data={"file": f}, **auth_headers_a).status_code == 202

    initial_embed_logs = LLMUsageLog.objects.filter(operation=LLMUsageLog.Operation.EMBEDDING).count()
    out = StringIO()
    call_command("reindex_embeddings", "--tenant-slug", "acme", "--batch-size", "16", stdout=out)
    assert "Re-embedded 1/1 document chunks." in out.getvalue()
    assert DocumentChunk.objects.filter(topic=topic_a).count() == 1
    assert LLMUsageLog.objects.filter(operation=LLMUsageLog.Operation.EMBEDDING).count() == initial_embed_logs + 1
