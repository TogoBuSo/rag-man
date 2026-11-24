from __future__ import annotations

import hashlib
import json
import math
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.conf import settings

from apps.tenancy.models import Tenant, TenantAPIKey
from apps.topics.models import Topic


def deterministic_embedding(text: str, dims: int = 1536) -> list[float]:
    """Produce a deterministic unit-normalized vector with keyword-aligned dimensions."""
    vec = [0.0] * dims
    tokens = text.lower().split()
    if not tokens:
        vec[0] = 1.0
        return vec
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dims
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@pytest.fixture(autouse=True)
def _test_settings(settings: Any) -> None:
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture
def tenant_a(db: Any) -> tuple[Tenant, str]:
    tenant = Tenant.objects.create(
        name="Acme Corp",
        slug="acme",
        default_synthesis_prompt="Synthesize across Acme topics with citations.",
    )
    _, raw_key = TenantAPIKey.create_key(tenant=tenant, name="primary")
    return tenant, raw_key


@pytest.fixture
def tenant_b(db: Any) -> tuple[Tenant, str]:
    tenant = Tenant.objects.create(
        name="Beta LLC",
        slug="beta",
        default_synthesis_prompt="Synthesize across Beta topics.",
    )
    _, raw_key = TenantAPIKey.create_key(tenant=tenant, name="primary")
    return tenant, raw_key


@pytest.fixture
def auth_headers_a(tenant_a: tuple[Tenant, str]) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {tenant_a[1]}"}


@pytest.fixture
def auth_headers_b(tenant_b: tuple[Tenant, str]) -> dict[str, str]:
    return {"HTTP_X_API_KEY": tenant_b[1]}


@pytest.fixture
def topic_a(tenant_a: tuple[Tenant, str]) -> Topic:
    return Topic.objects.create(
        tenant=tenant_a[0],
        slug="security-policies",
        name="Security Policies",
        system_description="Internal information security and compliance policies.",
        glossary={"SOC2": "Service Organization Control 2"},
        semantic_rules={"include": ["security", "compliance"], "exclude": ["recipes"]},
        min_semantic_score=0.75,
        metadata_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "department": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["department", "year"],
        },
        failure_policy=Topic.FailurePolicy.QUARANTINE,
        synthesis_prompt="Answer strictly from Security Policies.",
    )


@pytest.fixture
def topic_a_hr(tenant_a: tuple[Tenant, str]) -> Topic:
    return Topic.objects.create(
        tenant=tenant_a[0],
        slug="hr-handbook",
        name="HR Handbook",
        system_description="Human resources, PTO, and employee benefits handbook.",
        glossary={"PTO": "Paid Time Off"},
        semantic_rules={"include": ["benefits", "leave", "pto"], "exclude": ["infrastructure"]},
        min_semantic_score=0.75,
        metadata_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "department": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["department", "year"],
        },
        failure_policy=Topic.FailurePolicy.QUARANTINE,
    )


@pytest.fixture
def mock_litellm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mock litellm.completion, litellm.embedding, and litellm.rerank deterministically."""
    state: dict[str, Any] = {
        "gate_response": {"is_on_topic": True, "confidence_score": 0.92, "rationale": "Matches topic."},
        "extraction_response": {"department": "security", "year": 2026},
        "suggestion_response": {"suggested_topics": []},
        "synthesis_answer": "Grounded answer citing retrieved chunks.",
        "stream_chunks": ["Grounded ", "streaming ", "answer."],
        "captured_messages": [],
    }

    def fake_completion(**kwargs: Any) -> Any:
        messages = kwargs.get("messages", [])
        state["captured_messages"].append(messages)
        system_content = messages[0]["content"] if messages else ""

        if kwargs.get("stream"):
            return [
                {"choices": [{"delta": {"content": part}}]}
                for part in state["stream_chunks"]
            ]

        if "semantic gatekeeper" in system_content.lower():
            content = json.dumps(state["gate_response"])
        elif "alternative topics" in system_content.lower():
            content = json.dumps(state["suggestion_response"])
        elif "structured metadata extractor" in system_content.lower():
            content = json.dumps(state["extraction_response"])
        else:
            content = state["synthesis_answer"]

        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }

    def fake_embedding(**kwargs: Any) -> dict[str, Any]:
        inputs = kwargs.get("input", [])
        dims = settings.DEFAULT_EMBEDDING_DIMENSIONS
        return {
            "data": [
                {"embedding": deterministic_embedding(str(text), dims=dims)}
                for text in inputs
            ],
            "usage": {"prompt_tokens": len(inputs) * 10},
        }

    def fake_rerank(**kwargs: Any) -> dict[str, Any]:
        docs = kwargs.get("documents", [])
        top_n = kwargs.get("top_n", len(docs))
        # Reverse order to prove reranker actually reorders candidates
        indices = list(range(len(docs) - 1, -1, -1))[:top_n]
        return {
            "results": [
                {"index": idx, "relevance_score": 0.99 - i * 0.05}
                for i, idx in enumerate(indices)
            ]
        }

    monkeypatch.setattr("litellm.completion", fake_completion)
    monkeypatch.setattr("litellm.embedding", fake_embedding)
    monkeypatch.setattr("litellm.rerank", fake_rerank)
    monkeypatch.setattr("litellm.completion_cost", MagicMock(return_value=0.0001))
    return state
