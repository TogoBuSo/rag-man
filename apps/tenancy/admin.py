from django.contrib import admin

from apps.tenancy.models import Tenant, TenantAPIKey, TenantProviderConfig


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
    list_display = ("tenant", "default_extraction_model", "default_synthesis_model")
