from django.contrib import admin

from apps.topics.models import Topic


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "tenant", "is_active", "failure_policy", "schema_version")
    list_filter = ("tenant", "is_active", "failure_policy")
    search_fields = ("name", "slug")
