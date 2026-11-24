from __future__ import annotations

from django.http import HttpRequest
from django.utils import timezone
from ninja.security.base import AuthBase

from apps.tenancy.models import Tenant, TenantAPIKey


class TenantAuth(AuthBase):
    """Bearer / X-API-Key authenticator that resolves and attaches request.tenant."""

    openapi_type: str = "http"
    openapi_scheme: str = "bearer"

    def _extract_token(self, request: HttpRequest) -> str | None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            parts = auth_header.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
                if token:
                    return token
        api_key = request.headers.get("X-API-Key", "").strip()
        if api_key:
            return api_key
        return None

    def __call__(self, request: HttpRequest) -> Tenant | None:
        token = self._extract_token(request)
        if not token:
            return None

        key_hash = TenantAPIKey.hash_key(token)
        try:
            api_key = TenantAPIKey.objects.select_related("tenant").get(key_hash=key_hash)
        except TenantAPIKey.DoesNotExist:
            return None

        if not api_key.is_valid():
            return None

        TenantAPIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        request.tenant = api_key.tenant  # type: ignore[attr-defined]
        return api_key.tenant
