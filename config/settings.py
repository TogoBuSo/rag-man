from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-only-set-in-env")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "http://localhost:8085,http://127.0.0.1:8085",
    ).split(",")
    if o.strip()
]

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "pgvector.django",
    "apps.tenancy",
    "apps.topics",
    "apps.ingestion",
    "apps.quarantine",
    "apps.retrieval",
    "apps.llm_gateway",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.SimpleCORSMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "rag_platform"),
        "USER": os.environ.get("POSTGRES_USER", "rag_user"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "rag_password"),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Africa/Johannesburg")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Encryption key for django-encrypted-model-fields (32-byte url-safe base64 Fernet key)
FIELD_ENCRYPTION_KEY = os.environ.get(
    "FIELD_ENCRYPTION_KEY",
    "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
)

# Storage: primary mechanism is local temp directory on the webserver; AWS S3 is optional
USE_S3_STORAGE = os.environ.get("USE_S3_STORAGE", "false").lower() in ("1", "true", "yes")
LOCAL_STORAGE_TEMP_DIR = Path(
    os.environ.get("LOCAL_STORAGE_TEMP_DIR", str(Path(tempfile.gettempdir()) / "rag_man_uploads"))
)
CLEANUP_TEMP_FILES_AFTER_PROCESSING = os.environ.get(
    "CLEANUP_TEMP_FILES_AFTER_PROCESSING", "true"
).lower() in ("1", "true", "yes")

if USE_S3_STORAGE:
    INSTALLED_APPS.append("storages")
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "access_key": os.environ.get("AWS_ACCESS_KEY_ID", ""),
                "secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                "bucket_name": os.environ.get("AWS_STORAGE_BUCKET_NAME", ""),
                "endpoint_url": os.environ.get("AWS_S3_ENDPOINT_URL") or None,
                "region_name": os.environ.get("AWS_S3_REGION_NAME", "us-east-1"),
                "file_overwrite": True,
                "default_acl": None,
                "querystring_auth": False,
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    LOCAL_STORAGE_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {
                "location": str(LOCAL_STORAGE_TEMP_DIR),
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

# Celery / Redis
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_TASK_ALWAYS_EAGER", "false").lower() in (
    "1",
    "true",
    "yes",
)

# LiteLLM & Uniform Embedding Lock
DEFAULT_EMBEDDING_MODEL = os.environ.get("DEFAULT_EMBEDDING_MODEL", "text-embedding-3-small")
DEFAULT_EMBEDDING_DIMENSIONS = int(os.environ.get("DEFAULT_EMBEDDING_DIMENSIONS", "1536"))
DEFAULT_EMBEDDING_API_BASE = os.environ.get("DEFAULT_EMBEDDING_API_BASE", "")
DEFAULT_EMBEDDING_API_KEY = os.environ.get("DEFAULT_EMBEDDING_API_KEY", "")
DEFAULT_EXTRACTION_MODEL = os.environ.get("DEFAULT_EXTRACTION_MODEL", "gpt-4o-mini")
DEFAULT_SYNTHESIS_MODEL = os.environ.get("DEFAULT_SYNTHESIS_MODEL", "gpt-4o-mini")
DEFAULT_RERANK_MODEL = os.environ.get("DEFAULT_RERANK_MODEL", "cohere/rerank-english-v3.0")
LLM_REQUEST_TIMEOUT = float(os.environ.get("LLM_REQUEST_TIMEOUT", "60"))

# Optional provider defaults (set real keys/endpoints in .env only)
LLM_PROVIDERS = {
    "openai_compatible": {
        "api_base": os.environ.get("OPENAI_COMPATIBLE_API_BASE", ""),
        "api_key": os.environ.get("OPENAI_COMPATIBLE_API_KEY", ""),
    },
    "edenai": {
        "api_base": os.environ.get("EDENAI_API_BASE", "https://api.edenai.run/v2/llm"),
        "api_key": os.environ.get("EDENAI_API_KEY", ""),
    },
    "ollama": {
        "api_base": os.environ.get("OLLAMA_API_BASE", ""),
        "basic_auth": {
            "username": os.environ.get("OLLAMA_BASIC_AUTH_USERNAME", ""),
            "password": os.environ.get("OLLAMA_BASIC_AUTH_PASSWORD", ""),
        },
    },
}
