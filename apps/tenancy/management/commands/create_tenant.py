from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.tenancy.models import Tenant, TenantAPIKey


class Command(BaseCommand):
    help = "Create (or reuse) a Tenant and generate a new API key."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", required=True, help="Tenant display name")
        parser.add_argument("--slug", required=True, help="Unique tenant slug")
        parser.add_argument("--key-name", default="default", help="Label for the generated API key")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant, created = Tenant.objects.get_or_create(
            slug=options["slug"],
            defaults={"name": options["name"]},
        )
        _, raw_key = TenantAPIKey.create_key(tenant=tenant, name=options["key_name"])
        verb = "Created" if created else "Reused"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} tenant '{tenant.name}' (id={tenant.id}, slug={tenant.slug})"
            )
        )
        self.stdout.write(f"API Key (save this now, it will not be shown again): {raw_key}")
