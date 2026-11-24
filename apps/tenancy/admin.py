from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import admin

from apps.tenancy.models import Tenant, TenantAPIKey, TenantProviderConfig


class TenantProviderConfigAdminForm(forms.ModelForm):
    # OpenAI-Compatible Endpoint
    openai_compatible_api_base = forms.URLField(
        required=False,
        assume_scheme="https",
        label="API Base URL",
        help_text="e.g. https://api.edenai.run/v2/llm or https://your-vllm-host/v1 (use model prefix: openai_compatible/<model>)",
    )
    openai_compatible_api_key = forms.CharField(
        required=False,
        label="API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )

    # Eden AI (edenai.io)
    edenai_api_base = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Eden AI Base URL",
        help_text="Defaults to https://api.edenai.run/v2/llm (use model prefix: edenai/<provider>/<model>)",
    )
    edenai_api_key = forms.CharField(
        required=False,
        label="Eden AI API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )

    # Ollama (with Basic Auth)
    ollama_api_base = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Ollama Base URL",
        help_text="e.g. https://ollama.example.com (use model prefix: ollama/<model> or ollama_chat/<model>)",
    )
    ollama_basic_auth_username = forms.CharField(
        required=False,
        label="Basic Auth Username",
    )
    ollama_basic_auth_password = forms.CharField(
        required=False,
        label="Basic Auth Password",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing password.",
    )

    # Optional standard providers
    openai_api_key = forms.CharField(
        required=False,
        label="OpenAI API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )
    anthropic_api_key = forms.CharField(
        required=False,
        label="Anthropic API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )
    gemini_api_key = forms.CharField(
        required=False,
        label="Gemini API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )
    cohere_api_key = forms.CharField(
        required=False,
        label="Cohere API Key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep existing key.",
    )

    class Meta:
        model = TenantProviderConfig
        fields = [
            "tenant",
            "default_extraction_model",
            "default_synthesis_model",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.instance or not self.instance.pk:
            return

        oc = self.instance.get_provider_settings("openai_compatible")
        self.fields["openai_compatible_api_base"].initial = oc.get("api_base", "")
        if oc.get("api_key"):
            self.fields["openai_compatible_api_key"].help_text = "Key is configured. Leave blank to keep current key."

        eden = self.instance.get_provider_settings("edenai")
        self.fields["edenai_api_base"].initial = eden.get("api_base", "")
        if eden.get("api_key"):
            self.fields["edenai_api_key"].help_text = "Key is configured. Leave blank to keep current key."

        ollama = self.instance.get_provider_settings("ollama")
        self.fields["ollama_api_base"].initial = ollama.get("api_base", "")
        basic_auth = ollama.get("basic_auth") if isinstance(ollama.get("basic_auth"), dict) else {}
        self.fields["ollama_basic_auth_username"].initial = basic_auth.get("username", "")
        if basic_auth.get("password"):
            self.fields["ollama_basic_auth_password"].help_text = (
                "Password is configured. Leave blank to keep current password."
            )

        for provider_name in ("openai", "anthropic", "gemini", "cohere"):
            existing = self.instance.get_provider_settings(provider_name)
            if existing.get("api_key"):
                self.fields[f"{provider_name}_api_key"].help_text = (
                    "Key is configured. Leave blank to keep current key."
                )

    def save(self, commit: bool = True) -> TenantProviderConfig:
        instance: TenantProviderConfig = super().save(commit=False)

        # OpenAI-Compatible
        oc_existing = instance.get_provider_settings("openai_compatible")
        oc_base = (self.cleaned_data.get("openai_compatible_api_base") or "").strip()
        oc_key = (self.cleaned_data.get("openai_compatible_api_key") or "").strip() or oc_existing.get("api_key", "")
        if oc_base or oc_key:
            instance.set_provider_settings(
                "openai_compatible",
                {k: v for k, v in {"api_base": oc_base, "api_key": oc_key}.items() if v},
            )

        # Eden AI
        eden_existing = instance.get_provider_settings("edenai")
        eden_base = (self.cleaned_data.get("edenai_api_base") or "").strip()
        eden_key = (self.cleaned_data.get("edenai_api_key") or "").strip() or eden_existing.get("api_key", "")
        if eden_base or eden_key:
            instance.set_provider_settings(
                "edenai",
                {k: v for k, v in {"api_base": eden_base, "api_key": eden_key}.items() if v},
            )

        # Ollama + Basic Auth
        ollama_existing = instance.get_provider_settings("ollama")
        existing_ba = (
            ollama_existing.get("basic_auth")
            if isinstance(ollama_existing.get("basic_auth"), dict)
            else {}
        )
        ollama_base = (self.cleaned_data.get("ollama_api_base") or "").strip()
        ollama_user = (self.cleaned_data.get("ollama_basic_auth_username") or "").strip()
        ollama_pass = (
            (self.cleaned_data.get("ollama_basic_auth_password") or "").strip()
            or existing_ba.get("password", "")
        )
        if ollama_base or ollama_user or ollama_pass:
            ollama_cfg: dict[str, Any] = {}
            if ollama_base:
                ollama_cfg["api_base"] = ollama_base
            if ollama_user:
                ollama_cfg["basic_auth"] = {"username": ollama_user, "password": ollama_pass}
            instance.set_provider_settings("ollama", ollama_cfg)

        # Standard cloud providers
        for provider_name in ("openai", "anthropic", "gemini", "cohere"):
            new_key = (self.cleaned_data.get(f"{provider_name}_api_key") or "").strip()
            if new_key:
                instance.set_provider_settings(provider_name, {"api_key": new_key})

        if commit:
            instance.save()
        return instance


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    list_filter = ("is_active",)


@admin.register(TenantAPIKey)
class TenantAPIKeyAdmin(admin.ModelAdmin):
    list_display = ("tenant", "name", "key_prefix", "is_active", "expires_at", "last_used_at")
    list_filter = ("is_active", "tenant")
    readonly_fields = ("key_prefix", "key_hash", "created_at", "last_used_at")


@admin.register(TenantProviderConfig)
class TenantProviderConfigAdmin(admin.ModelAdmin):
    form = TenantProviderConfigAdminForm
    list_display = (
        "tenant",
        "default_extraction_model",
        "default_synthesis_model",
        "configured_providers",
    )
    fieldsets = (
        (
            "Tenant & Default Models",
            {
                "fields": (
                    "tenant",
                    "default_extraction_model",
                    "default_synthesis_model",
                )
            },
        ),
        (
            "OpenAI-Compatible Endpoint",
            {
                "fields": (
                    "openai_compatible_api_base",
                    "openai_compatible_api_key",
                )
            },
        ),
        (
            "Eden AI (edenai.io)",
            {
                "fields": (
                    "edenai_api_base",
                    "edenai_api_key",
                )
            },
        ),
        (
            "Ollama (with Basic Authentication)",
            {
                "fields": (
                    "ollama_api_base",
                    "ollama_basic_auth_username",
                    "ollama_basic_auth_password",
                )
            },
        ),
        (
            "Standard Cloud Providers (Optional BYOK)",
            {
                "classes": ("collapse",),
                "fields": (
                    "openai_api_key",
                    "anthropic_api_key",
                    "gemini_api_key",
                    "cohere_api_key",
                ),
            },
        ),
    )

    @admin.display(description="Configured Providers")
    def configured_providers(self, obj: TenantProviderConfig) -> str:
        keys = sorted(obj.get_api_keys().keys())
        return ", ".join(keys) if keys else "—"
