from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Any

from django.db import models
from django.utils import timezone
from encrypted_model_fields.fields import EncryptedTextField

from apps.tenancy.managers import TenantScopedManager


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    default_synthesis_prompt = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"


class TenantAPIKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=128, default="default")
    key_prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.tenant.slug}:{self.name} ({self.key_prefix}...)"

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def create_key(
        cls,
        tenant: Tenant,
        name: str = "default",
        expires_at: Any | None = None,
    ) -> tuple[TenantAPIKey, str]:
        raw_key = f"rag_{secrets.token_urlsafe(32)}"
        instance = cls.objects.create(
            tenant=tenant,
            name=name,
            key_prefix=raw_key[:12],
            key_hash=cls.hash_key(raw_key),
            expires_at=expires_at,
        )
        return instance, raw_key

    def is_valid(self) -> bool:
        if not self.is_active or not self.tenant.is_active:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True


class TenantProviderConfig(models.Model):
    """BYOK overrides for LiteLLM per tenant."""

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="provider_config")
    default_extraction_model = models.CharField(max_length=128, blank=True, default="")
    default_synthesis_model = models.CharField(max_length=128, blank=True, default="")
    # Encrypted JSON map of provider -> API key
    encrypted_api_keys = EncryptedTextField(blank=True, default="")

    def __str__(self) -> str:
        return f"ProviderConfig({self.tenant.slug})"

    def get_api_keys(self) -> dict[str, Any]:
        if not self.encrypted_api_keys:
            return {}
        try:
            data = json.loads(self.encrypted_api_keys)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def set_api_keys(self, keys: dict[str, Any]) -> None:
        self.encrypted_api_keys = json.dumps(keys)

    def get_provider_settings(self, provider: str) -> dict[str, Any]:
        """Return normalized provider settings (api_key, api_base, basic_auth, extra_headers)."""
        raw = self.get_api_keys().get(provider)
        if isinstance(raw, str):
            return {"api_key": raw}
        if isinstance(raw, dict):
            return dict(raw)
        return {}

    def set_provider_settings(self, provider: str, provider_settings: dict[str, Any]) -> None:
        all_settings = self.get_api_keys()
        all_settings[provider] = provider_settings
        self.set_api_keys(all_settings)
