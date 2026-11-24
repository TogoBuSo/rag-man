import os

from celery import Celery
from kombu import Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("rag_platform")
app.config_from_object("django.conf:settings", namespace="CELERY")

app.conf.task_queues = (
    Queue("default"),
    Queue("parsing"),
    Queue("llm_io"),
    Queue("indexing"),
)
app.conf.task_default_queue = "default"
app.conf.task_routes = {
    "apps.ingestion.tasks.parse_document_task": {"queue": "parsing"},
    "apps.ingestion.tasks.validate_and_extract_task": {"queue": "llm_io"},
    "apps.ingestion.tasks.index_document_task": {"queue": "indexing"},
}

app.autodiscover_tasks()
