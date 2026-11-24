from __future__ import annotations

import base64
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
        prefix = model.split("/", 1)[0].lower()
        if prefix in ("ollama", "ollama_chat"):
            return "ollama"
        if prefix in ("openai_compatible", "openai-compatible"):
            return "openai_compatible"
        if prefix == "edenai":
            return "edenai"
        return prefix
    lower = model.lower()
    if lower.startswith(("claude",)):
        return "anthropic"
    if lower.startswith(("gemini",)):
        return "gemini"
    if lower.startswith(("command", "rerank")):
        return "cohere"
    return "openai"


def _normalize_litellm_model(model: str, provider: str) -> str:
    """Translate custom provider prefixes (e.g. openai_compatible/<model>) to LiteLLM format."""
    if provider == "openai_compatible" and "/" in model:
        _, target_model = model.split("/", 1)
        return f"openai/{target_model}"
    return model


def _build_basic_auth_header(basic_auth: dict[str, Any] | None) -> str | None:
    if not isinstance(basic_auth, dict):
        return None
    username = str(basic_auth.get("username") or "").strip()
    password = str(basic_auth.get("password") or "")
    if not username:
        return None
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


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


def resolve_provider_kwargs(
    tenant: Tenant, model: str, role: str = "extraction"
) -> tuple[str, dict[str, Any]]:
    """Resolve LiteLLM model string and provider kwargs (api_key, api_base, extra_headers)."""
    provider = _infer_provider(model)
    litellm_model = _normalize_litellm_model(model, provider)

    # 1. Start with system-level defaults from settings.LLM_PROVIDERS
    sys_providers: dict[str, Any] = getattr(settings, "LLM_PROVIDERS", {})
    merged: dict[str, Any] = {}
    if provider in sys_providers and isinstance(sys_providers[provider], dict):
        merged.update(sys_providers[provider])

    # Embedding-specific system overrides (for OpenAI-compatible / Eden AI / Ollama embeddings)
    if role == "embedding":
        embed_base = getattr(settings, "DEFAULT_EMBEDDING_API_BASE", "")
        embed_key = getattr(settings, "DEFAULT_EMBEDDING_API_KEY", "")
        if embed_base:
            merged["api_base"] = embed_base
        if embed_key:
            merged["api_key"] = embed_key

    # 2. Overlay tenant BYOK encrypted settings (not overriding system embedding lock unless unset)
    if role != "embedding":
        try:
            tenant_cfg = tenant.provider_config
        except TenantProviderConfig.DoesNotExist:
            tenant_cfg = None
        if tenant_cfg is not None:
            for lookup_key in (provider, model):
                override = tenant_cfg.get_provider_settings(lookup_key)
                if override:
                    for k, v in override.items():
                        if v:
                            merged[k] = v

    kwargs: dict[str, Any] = {}
    api_key = str(merged.get("api_key") or "").strip()
    api_base = str(merged.get("api_base") or "").strip()
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    headers: dict[str, str] = {}
    raw_headers = merged.get("extra_headers")
    if isinstance(raw_headers, dict):
        headers.update({str(k): str(v) for k, v in raw_headers.items()})

    basic_header = _build_basic_auth_header(merged.get("basic_auth"))
    if basic_header and "Authorization" not in headers:
        headers["Authorization"] = basic_header

    if headers:
        kwargs["extra_headers"] = headers

    return litellm_model, kwargs


def resolve_api_key(tenant: Tenant, model: str) -> str | None:
    _, kwargs = resolve_provider_kwargs(tenant, model)
    return kwargs.get("api_key")


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
    """Centralized LiteLLM wrapper with BYOK, OpenAI-compatible, Eden AI, and Ollama Basic Auth support."""

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
        configured_model = resolve_model(tenant, topic, role)
        litellm_model, provider_kwargs = resolve_provider_kwargs(tenant, configured_model, role=role)
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            **provider_kwargs,
        }
        if response_schema:
            kwargs["extra_body"] = {"metadata_schema": response_schema}

        response = litellm.completion(**kwargs)
        prompt_tokens, completion_tokens = _extract_usage(response)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=operation,
            model=configured_model,
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
        configured_model = resolve_model(tenant, topic, role)
        litellm_model, provider_kwargs = resolve_provider_kwargs(tenant, configured_model, role=role)
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": 0.1,
            **provider_kwargs,
        }

        response = litellm.completion(**kwargs)
        prompt_tokens, completion_tokens = _extract_usage(response)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=operation,
            model=configured_model,
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
        configured_model = resolve_model(tenant, topic, role)
        litellm_model, provider_kwargs = resolve_provider_kwargs(tenant, configured_model, role=role)
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": 0.1,
            "stream": True,
            **provider_kwargs,
        }

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
            model=configured_model,
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
        configured_model = resolve_model(tenant, topic, "embedding")
        litellm_model, provider_kwargs = resolve_provider_kwargs(
            tenant, configured_model, role="embedding"
        )
        response = litellm.embedding(model=litellm_model, input=texts, **provider_kwargs)
        prompt_tokens, _ = _extract_usage(response)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=LLMUsageLog.Operation.EMBEDDING,
            model=configured_model,
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
        configured_model = resolve_model(tenant, topic, "rerank")
        litellm_model, provider_kwargs = resolve_provider_kwargs(tenant, configured_model, role="rerank")
        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            **provider_kwargs,
        }

        response = litellm.rerank(**kwargs)
        _log_usage(
            tenant=tenant,
            topic=topic,
            operation=LLMUsageLog.Operation.RERANK,
            model=configured_model,
        )
        results = response["results"] if isinstance(response, dict) else response.results
        ranked: list[tuple[int, float]] = []
        for item in results:
            idx = item["index"] if isinstance(item, dict) else item.index
            score = item["relevance_score"] if isinstance(item, dict) else item.relevance_score
            ranked.append((int(idx), float(score)))
        return ranked


llm_gateway = LLMGatewayClient()
