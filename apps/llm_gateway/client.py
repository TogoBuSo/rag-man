from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import litellm
from django.conf import settings

from apps.llm_gateway.models import LLMUsageLog
from apps.tenancy.models import Tenant, TenantProviderConfig
from apps.topics.models import Topic

logger = logging.getLogger(__name__)


def _infer_provider(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[0].lower()
    lower = model.lower()
    if lower.startswith(("claude",)):
        return "anthropic"
    if lower.startswith(("gemini",)):
        return "gemini"
    if lower.startswith(("command", "rerank")):
        return "cohere"
    return "openai"


def resolve_model(tenant: Tenant, topic: Topic | None, role: str) -> str:
    """Resolve model name honoring Topic -> Tenant -> System defaults, locking embeddings globally."""
    if role == "embedding":
        return str(settings.DEFAULT_EMBEDDING_MODEL)
    if role == "rerank":
        return str(settings.DEFAULT_RERANK_MODEL)

    provider_config: TenantProviderConfig | None = None
    try:
        provider_config = tenant.provider_config
    except TenantProviderConfig.DoesNotExist:
        provider_config = None

    if role == "extraction":
        if topic and topic.extraction_model:
            return topic.extraction_model
        if provider_config and provider_config.default_extraction_model:
            return provider_config.default_extraction_model
        return str(settings.DEFAULT_EXTRACTION_MODEL)

    if role == "synthesis":
        if topic and topic.synthesis_model:
            return topic.synthesis_model
        if provider_config and provider_config.default_synthesis_model:
            return provider_config.default_synthesis_model
        return str(settings.DEFAULT_SYNTHESIS_MODEL)

    raise ValueError(f"Unsupported LLM role: {role}")


def resolve_api_key(tenant: Tenant, model: str) -> str | None:
    try:
        provider_config = tenant.provider_config
    except TenantProviderConfig.DoesNotExist:
        return None
    keys = provider_config.get_api_keys()
    provider = _infer_provider(model)
    return keys.get(provider) or keys.get(model) or None


def _extract_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def _estimate_cost(response: Any) -> Decimal:
    try:
        cost = litellm.completion_cost(completion_response=response)
        return Decimal(str(round(float(cost), 6)))
    except Exception:
        return Decimal("0")


def _log_usage(
    tenant: Tenant,
    topic: Topic | None,
    operation: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated_cost_usd: Decimal = Decimal("0"),
) -> LLMUsageLog:
    return LLMUsageLog.objects.create(
        tenant=tenant,
        topic=topic,
        operation=operation,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


def _extract_content(response: Any) -> str:
    if isinstance(response, dict):
        return str(response["choices"][0]["message"]["content"] or "")
    return str(response.choices[0].message.content or "")


def _parse_json_content(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object from LLM response.")
    return parsed


class LLMGatewayClient:
    """Centralized LiteLLM wrapper with BYOK resolution and usage logging."""

    def complete_json(
        self,
        *,
        tenant: Tenant,
        topic: Topic | None,
        operation: str,
        messages: list[dict[str, str]],
        role: str = "extraction",
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = resolve_model(tenant, topic, role)
        api_key = resolve_api_key(tenant, model)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        if api_key:
            kwargs["api_key"] = api_key
        if response_schema:
            kwargs["extra_body"] = {"metadata_schema": response_schema}

        response = litellm.completion(**kwargs)
        prompt_tokens, completion_tokens = _extract_usage(response)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=operation,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=_estimate_cost(response),
        )
        return _parse_json_content(_extract_content(response))

    def complete_text(
        self,
        *,
        tenant: Tenant,
        topic: Topic | None,
        operation: str,
        messages: list[dict[str, str]],
        role: str = "synthesis",
    ) -> str:
        model = resolve_model(tenant, topic, role)
        api_key = resolve_api_key(tenant, model)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
        }
        if api_key:
            kwargs["api_key"] = api_key

        response = litellm.completion(**kwargs)
        prompt_tokens, completion_tokens = _extract_usage(response)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=operation,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=_estimate_cost(response),
        )
        return _extract_content(response)

    def stream_text(
        self,
        *,
        tenant: Tenant,
        topic: Topic | None,
        operation: str,
        messages: list[dict[str, str]],
        role: str = "synthesis",
    ) -> Iterator[str]:
        model = resolve_model(tenant, topic, role)
        api_key = resolve_api_key(tenant, model)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "stream": True,
        }
        if api_key:
            kwargs["api_key"] = api_key

        stream = litellm.completion(**kwargs)
        emitted_tokens = 0
        for chunk in stream:
            delta_text = ""
            if isinstance(chunk, dict):
                choices = chunk.get("choices") or []
                if choices:
                    delta_text = str(choices[0].get("delta", {}).get("content") or "")
            else:
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    delta_text = str(getattr(delta, "content", "") or "")
            if delta_text:
                emitted_tokens += max(1, len(delta_text.split()))
                yield delta_text

        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=operation,
            model=model,
            prompt_tokens=0,
            completion_tokens=emitted_tokens,
            estimated_cost_usd=Decimal("0"),
        )

    def embed_texts(
        self,
        *,
        tenant: Tenant,
        topic: Topic | None,
        texts: list[str],
    ) -> list[list[float]]:
        if not texts:
            return []
        model = resolve_model(tenant, topic, "embedding")
        response = litellm.embedding(model=model, input=texts)
        prompt_tokens, _ = _extract_usage(response)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=LLMUsageLog.Operation.EMBEDDING,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            estimated_cost_usd=Decimal("0"),
        )
        data = response["data"] if isinstance(response, dict) else response.data
        vectors: list[list[float]] = []
        for item in data:
            vec = item["embedding"] if isinstance(item, dict) else item.embedding
            if len(vec) != settings.DEFAULT_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {settings.DEFAULT_EMBEDDING_DIMENSIONS}, got {len(vec)}"
                )
            vectors.append([float(v) for v in vec])
        return vectors

    def rerank(
        self,
        *,
        tenant: Tenant,
        topic: Topic | None,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        model = resolve_model(tenant, topic, "rerank")
        api_key = resolve_api_key(tenant, model)
        kwargs: dict[str, Any] = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        if api_key:
            kwargs["api_key"] = api_key

        response = litellm.rerank(**kwargs)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=LLMUsageLog.Operation.RERANK,
            model=model,
        )
        results = response["results"] if isinstance(response, dict) else response.results
        ranked: list[tuple[int, float]] = []
        for item in results:
            idx = item["index"] if isinstance(item, dict) else item.index
            score = item["relevance_score"] if isinstance(item, dict) else item.relevance_score
            ranked.append((int(idx), float(score)))
        return ranked


llm_gateway = LLMGatewayClient()
