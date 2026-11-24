from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.topics.models import Topic


@pytest.mark.django_db
def test_filtered_hybrid_retrieval_and_synthesis(
    client: Client,
    topic_a: Topic,
    topic_a_hr: Topic,
    auth_headers_a: dict[str, str],
    mock_litellm: dict[str, Any],
) -> None:
    # Ingest 2025 security doc
    mock_litellm["extraction_response"] = {"department": "security", "year": 2025}
    f1 = SimpleUploadedFile(
        "sec_2025.md",
        b"# Password Policy 2025\nPasswords must be rotated every 90 days for SOC2.",
        content_type="text/markdown",
    )
    assert client.post(f"/api/v1/topics/{topic_a.id}/documents/upload", data={"file": f1}, **auth_headers_a).status_code == 202

    # Ingest 2026 security doc
    mock_litellm["extraction_response"] = {"department": "security", "year": 2026}
    f2 = SimpleUploadedFile(
        "sec_2026.md",
        b"# Zero Trust Policy 2026\nHardware passkeys replace password rotation for SOC2 compliance.",
        content_type="text/markdown",
    )
    assert client.post(f"/api/v1/topics/{topic_a.id}/documents/upload", data={"file": f2}, **auth_headers_a).status_code == 202

    # Ingest 2026 HR doc
    mock_litellm["extraction_response"] = {"department": "hr", "year": 2026}
    f3 = SimpleUploadedFile(
        "hr_2026.md",
        b"# Remote Work PTO 2026\nRemote employees receive 25 days PTO and hardware stipends.",
        content_type="text/markdown",
    )
    assert client.post(f"/api/v1/topics/{topic_a_hr.id}/documents/upload", data={"file": f3}, **auth_headers_a).status_code == 202

    # 1. Filtered hybrid retrieval: year >= 2026 should exclude 2025 doc
    ret_resp = client.post(
        "/api/v1/rag/retrieve",
        data=json.dumps({
            "query": "SOC2 passkeys hardware",
            "topic_ids": [str(topic_a.id)],
            "top_k": 5,
            "hybrid_alpha": 0.5,
            "metadata_filters": [{"field": "year", "operator": "gte", "value": 2026}],
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert ret_resp.status_code == 200
    chunks = ret_resp.json()
    assert len(chunks) == 1
    assert chunks[0]["source_uri"] == "sec_2026.md"
    assert chunks[0]["metadata"]["year"] == 2026

    # 2. Invalid metadata filter field rejected with 422
    bad_filter = client.post(
        "/api/v1/rag/retrieve",
        data=json.dumps({
            "query": "SOC2",
            "topic_ids": [str(topic_a.id)],
            "metadata_filters": [{"field": "year; DROP TABLE", "operator": "eq", "value": 2026}],
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert bad_filter.status_code == 422

    # 3. Rerank=True path
    rerank_resp = client.post(
        "/api/v1/rag/retrieve",
        data=json.dumps({
            "query": "SOC2 policy",
            "topic_ids": [str(topic_a.id)],
            "top_k": 2,
            "rerank": True,
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert rerank_resp.status_code == 200
    assert len(rerank_resp.json()) == 2

    # 4. Multi-topic synthesis (JSON) verifies cross-topic chunk prefixing & merged glossary
    mock_litellm["captured_messages"].clear()
    synth_resp = client.post(
        "/api/v1/rag/synthesize",
        data=json.dumps({
            "query": "What are the 2026 hardware and PTO rules?",
            "topic_ids": [str(topic_a.id), str(topic_a_hr.id)],
            "top_k": 4,
            "metadata_filters": [{"field": "year", "operator": "eq", "value": 2026}],
            "stream": False,
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert synth_resp.status_code == 200
    body = synth_resp.json()
    assert body["answer"] == "Grounded answer citing retrieved chunks."
    assert len(body["citations"]) == 2
    last_msgs = mock_litellm["captured_messages"][-1]
    assert "SOC2" in last_msgs[0]["content"] and "PTO" in last_msgs[0]["content"]
    assert "[Topic: Security Policies | Source: sec_2026.md | Chunk:" in last_msgs[1]["content"]
    assert "[Topic: HR Handbook | Source: hr_2026.md | Chunk:" in last_msgs[1]["content"]

    # 5. Streaming SSE synthesis
    sse_resp = client.post(
        "/api/v1/rag/synthesize",
        data=json.dumps({
            "query": "Summarize SOC2 passkeys",
            "topic_ids": [str(topic_a.id)],
            "stream": True,
        }),
        content_type="application/json",
        **auth_headers_a,
    )
    assert sse_resp.status_code == 200
    assert sse_resp["Content-Type"].startswith("text/event-stream")
    stream_text = b"".join(sse_resp.streaming_content).decode("utf-8")
    assert '"type": "citations"' in stream_text
    assert '"content": "Grounded "' in stream_text
    assert "data: [DONE]" in stream_text
