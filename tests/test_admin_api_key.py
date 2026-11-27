from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.tenancy.models import Tenant, TenantAPIKey


@pytest.fixture
def admin_client(db: Any) -> Client:
    User = get_user_model()
    user = User.objects.create_superuser(username="apikey_admin", email="admin@example.com", password="pw")
    c = Client()
    c.force_login(user)
    return c


@pytest.mark.django_db
def test_admin_create_api_key_generates_and_displays_key_once(
    admin_client: Client,
    tenant_a: tuple[Tenant, str],
) -> None:
    tenant, _ = tenant_a

    # Add page renders cleanly without empty key_prefix/key_hash fields
    add_resp = admin_client.get("/admin/tenancy/tenantapikey/add/")
    assert add_resp.status_code == 200

    # Creating a new API key generates key_prefix, key_hash, and displays the raw key once
    resp = admin_client.post(
        "/admin/tenancy/tenantapikey/add/",
        data={
            "tenant": str(tenant.id),
            "name": "ci-key",
            "is_active": "on",
        },
        follow=True,
    )
    assert resp.status_code == 200
    body = resp.content.decode("utf-8")

    match = re.search(r'id="new-tenant-api-key"[^>]*>(rag_[A-Za-z0-9_-]+)</code>', body)
    assert match is not None, "Expected newly generated API key to be displayed for copy"
    raw_key = match.group(1)

    key_obj = TenantAPIKey.objects.get(tenant=tenant, name="ci-key")
    assert key_obj.key_prefix == raw_key[:12]
    assert key_obj.key_hash == TenantAPIKey.hash_key(raw_key)

    # Generated key works for API authentication
    api_client = Client()
    auth_resp = api_client.get("/api/v1/topics", HTTP_AUTHORIZATION=f"Bearer {raw_key}")
    assert auth_resp.status_code == 200

    # Subsequent view / edit does NOT regenerate or re-display the raw key
    change_resp = admin_client.get(f"/admin/tenancy/tenantapikey/{key_obj.id}/change/")
    assert change_resp.status_code == 200
    assert raw_key not in change_resp.content.decode("utf-8")

    edit_resp = admin_client.post(
        f"/admin/tenancy/tenantapikey/{key_obj.id}/change/",
        data={
            "tenant": str(tenant.id),
            "name": "ci-key-renamed",
            "is_active": "on",
        },
        follow=True,
    )
    assert edit_resp.status_code == 200
    assert "new-tenant-api-key" not in edit_resp.content.decode("utf-8")
    key_obj.refresh_from_db()
    assert key_obj.name == "ci-key-renamed"
    assert key_obj.key_hash == TenantAPIKey.hash_key(raw_key)
