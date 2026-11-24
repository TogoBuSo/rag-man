from __future__ import annotations

import json
from typing import Any

import pytest
from django.test import Client

from apps.tenancy.models import Tenant


@pytest.mark.django_db
def test_topic_crud_and_schema_version_bump(
    client: Client,
    auth_headers_a: dict[str, str],
    auth_headers_b: dict[str, str],
    tenant_a: tuple[Tenant, str],
    tenant_b: tuple[Tenant, str],
) -> None:
    payload = {
        "slug": "legal-docs",
        "name": "Legal Docs",
        "system_description": "Contracts and NDAs.",
        "metadata_schema": {
            "type": "object",
            "properties": {"contract_id": {"type": "string"}},
            "required": ["contract_id"],
        },
        "failure_policy": "STRICT_REJECT",
    }
    resp = client.post(
        "/api/v1/topics",
        data=json.dumps(payload),
        content_type="application/json",
        **auth_headers_a,
    )
    assert resp.status_code == 201
    topic_data = resp.json()
    assert topic_data["schema_version"] == 1
    topic_id = topic_data["id"]

    # Duplicate slug for same tenant -> 409
    dup = client.post(
        "/api/v1/topics",
        data=json.dumps(payload),
        content_type="application/json",
        **auth_headers_a,
    )
    assert dup.status_code == 409

    # Same slug for another tenant -> 201
    other = client.post(
        "/api/v1/topics",
        data=json.dumps(payload),
        content_type="application/json",
        **auth_headers_b,
    )
    assert other.status_code == 201

    # PATCH without schema change keeps schema_version == 1
    patch_name = client.patch(
        f"/api/v1/topics/{topic_id}",
        data=json.dumps({"name": "Legal Contracts"}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert patch_name.status_code == 200
    assert patch_name.json()["schema_version"] == 1

    # PATCH with schema change increments schema_version -> 2
    new_schema = {
        "type": "object",
        "properties": {
            "contract_id": {"type": "string"},
            "signed_year": {"type": "integer"},
        },
        "required": ["contract_id"],
    }
    patch_schema = client.patch(
        f"/api/v1/topics/{topic_id}",
        data=json.dumps({"metadata_schema": new_schema}),
        content_type="application/json",
        **auth_headers_a,
    )
    assert patch_schema.status_code == 200
    assert patch_schema.json()["schema_version"] == 2


@pytest.mark.django_db
def test_topic_rejects_invalid_json_schema(client: Client, auth_headers_a: dict[str, str]) -> None:
    bad_payload = {
        "slug": "bad-schema",
        "name": "Bad Schema",
        "system_description": "Invalid schema test.",
        "metadata_schema": {"type": "not_a_real_json_schema_type"},
    }
    resp = client.post(
        "/api/v1/topics",
        data=json.dumps(bad_payload),
        content_type="application/json",
        **auth_headers_a,
    )
    assert resp.status_code == 422
