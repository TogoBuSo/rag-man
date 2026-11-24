from django.contrib import admin
from django.urls import path
from ninja import NinjaAPI

from apps.ingestion.api import router as ingestion_router
from apps.quarantine.api import router as quarantine_router
from apps.retrieval.api import router as retrieval_router
from apps.tenancy.auth import TenantAuth
from apps.topics.api import router as topics_router

api = NinjaAPI(
    title="Context-Enforced Multi-Tenant RAG Platform",
    version="1.0.0",
    auth=TenantAuth(),
)

api.add_router("", topics_router, tags=["Topics"])
api.add_router("", ingestion_router, tags=["Ingestion"])
api.add_router("", quarantine_router, tags=["Quarantine"])
api.add_router("", retrieval_router, tags=["RAG"])

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
]
