from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.db import models

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant


class TenantScopedQuerySet(models.QuerySet[Any]):
    def for_tenant(self, tenant: Tenant) -> Self:
        return self.filter(tenant=tenant)


class TenantScopedManager(models.Manager[Any]):
    def get_queryset(self) -> TenantScopedQuerySet:
        return TenantScopedQuerySet(self.model, using=self._db)

    def for_tenant(self, tenant: Tenant) -> TenantScopedQuerySet:
        return self.get_queryset().for_tenant(tenant)
