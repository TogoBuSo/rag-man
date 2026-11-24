from __future__ import annotations

import base64
from typing import Any

import pytest

from apps.llm_gateway.client import llm_gateway, resolve_provider_kwargs
from apps.llm_gateway.models import LLMUsageLog
from apps.tenancy.models import Tenant, TenantProviderConfig
from apps.topics.models import Topic


@pytest.mark.django_db
def test_openai_compatible_provider_resolution_and_call(
    tenant_a: tuple[Tenant, str],
    topic_a: Topic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = tenant_a[0]
    cfg = TenantProviderConfig.objects.create(
        tenant=tenant,
        default_synthesis_model="openai_compatible/openai/gpt-4o-mini",
    )
    cfg.set_provider_settings(
        "openai_compatible",
        {
            "api_base": "https://api.edenai.run/v2/llm",
            "api_key": "eden-compat-key",
        },
    )
    cfg.save()

    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr("litellm.completion", fake_completion)

    ans = llm_gateway.complete_text(
        tenant=tenant,
        topic=topic_a,
        operation=LLMUsageLog.Operation.SYNTHESIS,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert ans == "ok"
    assert captured["model"] == "openai/openai/gpt-4o-mini"
    assert captured["api_base"] == "https://api.edenai.run/v2/llm"
    assert captured["api_key"] == "eden-compat-key"


@pytest.mark.django_db
def test_edenai_provider_resolution_from_settings_and_byok(
    tenant_a: tuple[Tenant, str],
    settings: Any,
) -> None:
    tenant = tenant_a[0]
    settings.LLM_PROVIDERS = {
        "edenai": {
            "api_base": "https://api.edenai.run/v2/llm",
            "api_key": "sys-edenai-key",
        }
    }
    model, kwargs = resolve_provider_kwargs(tenant, "edenai/openai/gpt-4o-mini")
    assert model == "edenai/openai/gpt-4o-mini"
    assert kwargs["api_base"] == "https://api.edenai.run/v2/llm"
    assert kwargs["api_key"] == "sys-edenai-key"

    # Tenant BYOK overrides system key
    cfg = TenantProviderConfig.objects.create(tenant=tenant)
    cfg.set_provider_settings("edenai", {"api_key": "tenant-edenai-key"})
    cfg.save()

    _, kwargs_byok = resolve_provider_kwargs(tenant, "edenai/openai/gpt-4o-mini")
    assert kwargs_byok["api_key"] == "tenant-edenai-key"
    assert kwargs_byok["api_base"] == "https://api.edenai.run/v2/llm"


@pytest.mark.django_db
def test_ollama_provider_with_basic_auth_headers(
    tenant_a: tuple[Tenant, str],
    topic_a: Topic,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = tenant_a[0]
    cfg = TenantProviderConfig.objects.create(
        tenant=tenant,
        default_synthesis_model="ollama_chat/llama3.2",
    )
    cfg.set_provider_settings(
        "ollama",
        {
            "api_base": "https://ollama.internal.example",
            "basic_auth": {"username": "raguser", "password": "s3cr3t!"},
        },
    )
    cfg.save()

    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "ollama-ok"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4},
        }

    monkeypatch.setattr("litellm.completion", fake_completion)

    ans = llm_gateway.complete_text(
        tenant=tenant,
        topic=topic_a,
        operation=LLMUsageLog.Operation.SYNTHESIS,
        messages=[{"role": "user", "content": "hello"}],
    )
    expected_token = base64.b64encode(b"raguser:s3cr3t!").decode("ascii")
    assert ans == "ollama-ok"
    assert captured["model"] == "ollama_chat/llama3.2"
    assert captured["api_base"] == "https://ollama.internal.example"
    assert captured["extra_headers"]["Authorization"] == f"Basic {expected_token}"


@pytest.mark.django_db
def test_provider_config_admin_form_saves_and_preserves_secrets(
    tenant_a: tuple[Tenant, str],
) -> None:
    from apps.tenancy.admin import TenantProviderConfigAdminForm

    tenant = tenant_a[0]
    form = TenantProviderConfigAdminForm(
        data={
            "tenant": str(tenant.id),
            "default_extraction_model": "edenai/openai/gpt-4o-mini",
            "default_synthesis_model": "ollama_chat/llama3.2",
            "openai_compatible_api_base": "https://compat.example.com/v1",
            "openai_compatible_api_key": "compat-secret",
            "edenai_api_base": "https://api.edenai.run/v2/llm",
            "edenai_api_key": "eden-secret",
            "ollama_api_base": "https://ollama.example.com",
            "ollama_basic_auth_username": "adminuser",
            "ollama_basic_auth_password": "adminpassword",
        }
    )
    assert form.is_valid(), form.errors
    cfg = form.save()

    assert cfg.get_provider_settings("openai_compatible") == {
        "api_base": "https://compat.example.com/v1",
        "api_key": "compat-secret",
    }
    assert cfg.get_provider_settings("edenai") == {
        "api_base": "https://api.edenai.run/v2/llm",
        "api_key": "eden-secret",
    }
    assert cfg.get_provider_settings("ollama") == {
        "api_base": "https://ollama.example.com",
        "basic_auth": {"username": "adminuser", "password": "adminpassword"},
    }

    # Re-saving with blank secret fields preserves existing encrypted secrets
    form2 = TenantProviderConfigAdminForm(
        instance=cfg,
        data={
            "tenant": str(tenant.id),
            "default_extraction_model": "edenai/openai/gpt-4o-mini",
            "default_synthesis_model": "ollama_chat/llama3.2",
            "openai_compatible_api_base": "https://compat.example.com/v1",
            "openai_compatible_api_key": "",
            "edenai_api_base": "https://api.edenai.run/v2/llm",
            "edenai_api_key": "",
            "ollama_api_base": "https://ollama.example.com",
            "ollama_basic_auth_username": "adminuser",
            "ollama_basic_auth_password": "",
        },
    )
    assert form2.is_valid(), form2.errors
    cfg2 = form2.save()
    assert cfg2.get_provider_settings("edenai")["api_key"] == "eden-secret"
    assert cfg2.get_provider_settings("ollama")["basic_auth"]["password"] == "adminpassword"

