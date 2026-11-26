# rag-man

A **Context-Enforced Multi-Tenant Retrieval-Augmented Generation (RAG)** platform built with Django 5, Django Ninja, PostgreSQL 17 + `pgvector` 0.8+, LiteLLM, and Celery + Redis.

Every ingested document belongs to a tenant-scoped **Topic** that enforces three contracts before indexing:
1. **Hard Multi-Tenancy Isolation:** Enforced at the ORM (`TenantScopedManager`), API (`TenantAuth`), and retrieval layers.
2. **Semantic Gatekeeping:** Validates incoming documents against the Topic's description, glossary, and inclusion/exclusion rules.
3. **Structured Metadata Extraction:** Uses an LLM to extract and validate JSONB metadata against a Topic-specific **JSON Schema (Draft 2020-12)**.

---

## Key Features

* **3-Stage Ingestion Pipeline (Celery + Redis):**
  * `parsing` queue: `markitdown` file extraction (PDF, DOCX, Markdown, HTML, etc.) and SSRF-safe `trafilatura` URL fetching.
  * `llm_io` queue: Semantic gatekeeping, structured metadata extraction, `jsonschema` validation, and `STRICT_REJECT` vs. `QUARANTINE` policy gating (with automatic `suggested_topics` on semantic mismatch).
  * `indexing` queue: Header/page-aware recursive Markdown chunking, batched embeddings, and atomic PostgreSQL writes (`VectorField` + `SearchVector`).
* **Local Temp-Directory Processing (AWS S3 Optional):** Uploads are processed in a local temporary directory on the webserver (`LOCAL_STORAGE_TEMP_DIR`) and cleaned up automatically upon reaching a terminal state (`INDEXED` / `REJECTED`). S3/MinIO storage is opt-in via `USE_S3_STORAGE=true`.
* **Human-in-the-Loop (HITL) Quarantine Queue:** Resolve quarantined documents via `APPROVED_OVERRIDE`, `EDITED_AND_INDEXED`, `REASSIGNED`, or `REJECTED` through the API or Django Admin.
* **Hybrid Retrieval & Grounded Synthesis:**
  * Safe JSONB metadata filtering (`eq`, `ne`, `in`, `gt`, `gte`, `lt`, `lte`, `contains`).
  * PostgreSQL `pgvector` 0.8+ `hnsw.iterative_scan = 'relaxed_order'` + full-text search (`websearch` + `SearchRank`) merged via Reciprocal Rank Fusion (RRF), plus optional `litellm.rerank()`.
  * Single-topic and multi-topic grounded synthesis with inline citations and Server-Sent Events (SSE) streaming.
* **Pluggable LLM Gateway (LiteLLM + Encrypted BYOK):**
  * Supports OpenAI, Anthropic, Gemini, Cohere, **generic OpenAI-compatible endpoints**, **Eden AI (`edenai.io`)**, and **Ollama with HTTP Basic Authentication**.
  * System-wide uniform embedding model lock (`DEFAULT_EMBEDDING_MODEL` / `DEFAULT_EMBEDDING_DIMENSIONS`) with zero-downtime `manage.py reindex_embeddings`.
* **Full Django Admin Console (`/admin/`):** Manage Tenants, API Keys, Provider Endpoints & Auth, Topics, Document Ingestion (file/URL upload with async Celery or synchronous debug mode), Quarantine HITL reviews, Chunks, and LLM Usage Logs.

---

## Tech Stack & Directory Layout

```text
├── config/                  # Django settings (.env), Celery queues, URLs, CORS middleware
├── apps/
│   ├── tenancy/             # Tenant, TenantAPIKey, TenantProviderConfig (encrypted BYOK), TenantAuth
│   ├── topics/              # Topic CRUD, Draft 2020-12 JSON Schema validation & versioning
│   ├── ingestion/           # Document, DocumentChunk (pgvector HNSW + GIN), parsers, chunker, Celery tasks
│   ├── quarantine/          # QuarantineReview HITL queue & resolution services
│   ├── retrieval/           # Safe JSONB filter translator, hybrid RRF search, grounded synthesis + SSE
│   └── llm_gateway/         # LiteLLM client, BYOK + provider routing, prompts, LLMUsageLog
├── tests/                   # pytest-django verification suite
├── docker-compose.yml       # PostgreSQL 17 + pgvector, Redis 7 (optional S3 profile)
├── pyproject.toml
└── .env.example
```

---

## Quick Start

### 1. Create a Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Optional: install AWS S3 / django-storages support
.venv/bin/pip install -e ".[s3]"
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and set your secrets, domain, and provider keys. **Never commit `.env`.**
```bash
cp .env.example .env
```

Key settings in `.env`:
* `DJANGO_SECRET_KEY` & `FIELD_ENCRYPTION_KEY` (generate a 32-byte Fernet key via `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
* `DJANGO_ALLOWED_HOSTS` & `DJANGO_CSRF_TRUSTED_ORIGINS` (e.g., `https://your-domain.example.com` when running behind Nginx Proxy Manager on port `443`)
* `LOCAL_STORAGE_TEMP_DIR` & `CLEANUP_TEMP_FILES_AFTER_PROCESSING=true` (or `USE_S3_STORAGE=true` for S3)
* LLM provider endpoints and keys (see [Provider & Model Prefixes](#provider--model-prefixes))

### 3. Start PostgreSQL (`pgvector`) & Redis
```bash
docker compose up -d postgres redis

# Optional: include MinIO S3-compatible storage
docker compose --profile s3 up -d
```

### 4. Apply Migrations & Create Admin / Tenant
```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py create_tenant --name "Default Tenant" --slug default
```
Save the generated `rag_...` API key (it is stored hashed and displayed only once).

### 5. Run the Web Server & Celery Worker
```bash
# Django debug server on internal port 8085 (proxy port 443 -> 8085 via NPM/Nginx/Caddy)
.venv/bin/python manage.py runserver 0.0.0.0:8085

# Celery worker handling all ingestion queues
.venv/bin/celery -A config worker -Q default,parsing,llm_io,indexing --loglevel=info --concurrency=2
```

---

## Provider & Model Prefixes

All LLM and embedding calls route through `apps/llm_gateway/` backed by LiteLLM. Configure defaults in `.env` or per-tenant encrypted overrides in `/admin/tenancy/tenantproviderconfig/`.

| Provider | Model Prefix Example | Configuration |
| :--- | :--- | :--- |
| **OpenAI-Compatible Endpoint** | `openai_compatible/<model>` | `OPENAI_COMPATIBLE_API_BASE` + `OPENAI_COMPATIBLE_API_KEY` |
| **Eden AI (`edenai.io`)** | `edenai/<provider>/<model>` | `EDENAI_API_BASE` (`https://api.edenai.run/v2/llm`) + `EDENAI_API_KEY` |
| **Ollama (with Basic Auth)** | `ollama/<model>` or `ollama_chat/<model>` | `OLLAMA_API_BASE` + `OLLAMA_BASIC_AUTH_USERNAME` + `OLLAMA_BASIC_AUTH_PASSWORD` |
| **Standard Cloud Providers** | `gpt-4o-mini`, `anthropic/claude-...`, `gemini/...` | `OPENAI_API_KEY` or tenant BYOK keys |

* **Extraction & Synthesis models** must be chat/completion LLMs.
* **Embedding model** (`DEFAULT_EMBEDDING_MODEL` / `DEFAULT_EMBEDDING_DIMENSIONS`) is locked globally. After changing it, run:
  ```bash
  .venv/bin/python manage.py reindex_embeddings
  ```

---

## API Surface (`/api/v1/`)

Authenticate requests with either `Authorization: Bearer <api_key>` or `X-API-Key: <api_key>`. Interactive OpenAPI docs are available at `/api/v1/docs`.

| Group | Endpoint | Description |
| :--- | :--- | :--- |
| **Topics** | `POST /api/v1/topics` | Create a Topic (validates JSON Schema Draft 2020-12) |
| | `GET /api/v1/topics` | List active Topics for the authenticated tenant |
| | `PATCH /api/v1/topics/{topic_id}` | Update Topic context; auto-bumps `schema_version` on schema change |
| **Ingestion** | `POST /api/v1/topics/{topic_id}/documents/upload` | Multipart file upload (`202 Accepted`, or `200 OK` if deduplicated) |
| | `POST /api/v1/topics/{topic_id}/documents/text` | Multipart manual text (`title`, `content`, optional `attachments[]` ingested as child documents) |
| | `POST /api/v1/topics/{topic_id}/documents/url` | Submit URL `{url, force_reindex}` (`202 Accepted`) |
| | `GET /api/v1/documents/{document_id}` | Poll ingestion status, `extracted_metadata`, and `validation_report` |
| | `DELETE /api/v1/documents/{document_id}` | Hard-delete document, chunks, and stored files |
| **Quarantine** | `GET /api/v1/quarantine` | List `PENDING` quarantine reviews for the tenant |
| | `POST /api/v1/quarantine/{review_id}/resolve` | Resolve via `APPROVED_OVERRIDE`, `EDITED_AND_INDEXED`, `REASSIGNED`, or `REJECTED` |
| **RAG** | `POST /api/v1/rag/retrieve` | Hybrid dense + sparse RRF retrieval with JSONB metadata filters & optional reranking |
| | `POST /api/v1/rag/synthesize` | Grounded single-topic or multi-topic synthesis (JSON or `stream=true` SSE) |

---

## Running Tests

Ensure PostgreSQL (`pgvector`) is running (`docker compose up -d postgres`), then run:

```bash
.venv/bin/pytest
```
