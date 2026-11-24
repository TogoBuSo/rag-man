# Technical Specification: Context-Enforced Multi-Tenant RAG Pipeline

> **Target Environment:** Antigravity IDE (Agentic Coding Blueprint)  
> **Stack:** Django 5.x, Django Ninja, PostgreSQL 16/17 + `pgvector` 0.8+, LiteLLM, Celery + Redis, S3-compatible Storage

---

## 1. System Overview & Agent Directives

This application is a multi-tenant Retrieval-Augmented Generation (RAG) platform that manages both **structured metadata** and **vector embeddings** inside segmented **Topics**.

A **Topic** is a strict operational boundary belonging to a single **Tenant**. It enforces three contracts on every ingested document:
1. **Tenancy Isolation:** Hard isolation between tenants at the ORM, API, and query layers.
2. **Semantic Gatekeeping:** Validates incoming documents against the Topic's system description, glossary, and inclusion/exclusion rules.
3. **Structured Metadata Schema:** Uses LLM-assisted extraction to populate and validate structured JSONB metadata against a Topic-specific JSON Schema (Draft 2020-12).

### Coding & Architecture Directives
* **Python Version:** Python 3.12+ with strict type annotations across all services, tasks, and schemas.
* **API Layer:** Use **Django Ninja** with Pydantic v2 schemas. Keep views thin; place business logic in `services.py` modules.
* **Database:** All relational, JSONB, full-text (`tsvector`), and vector (`VectorField`) data lives in PostgreSQL.
* **LLM & Embeddings:** Route all LLM and embedding calls through a centralized `llm_gateway` app backed by **LiteLLM**.
  * **Embeddings:** Uniform system-wide default embedding model (fixed dimension, e.g., `1536`) across all Topics to guarantee vector index compatibility.
  * **Extraction & Synthesis LLMs:** Pluggable / BYOK per Tenant or Topic.
* **Idempotency:** Ingestion tasks must be idempotent and safe to retry via SHA-256 `content_hash` deduplication and atomic DB swaps.

---

## 2. Dependencies (`pyproject.toml` / `requirements.txt`)

```text
django>=5.1,<6.0
django-ninja>=1.3
pydantic>=2.8
psycopg[binary]>=3.2
pgvector>=0.3.6
celery>=5.4
redis>=5.0
litellm>=1.50
jsonschema>=4.23
boto3>=1.35
django-storages>=1.14
django-encrypted-model-fields>=0.6.5
trafilatura>=1.12
markitdown>=0.0.1
httpx>=0.27
pytest>=8.3
pytest-django>=4.9
```

---

## 3. Target Project Directory Structure

```text
rag_platform/
├── config/
│   ├── __init__.py          # Imports celery_app
│   ├── celery.py            # Celery app & queue routing (parsing, llm_io, indexing)
│   ├── settings.py          # Django, pgvector, Redis, S3, LiteLLM defaults
│   ├── urls.py              # Mounts Django Ninja API at /api/v1/
│   └── asgi.py
├── apps/
│   ├── tenancy/
│   │   ├── models.py        # Tenant, TenantAPIKey, TenantProviderConfig
│   │   ├── auth.py          # Async Bearer/API-Key Ninja authenticator
│   │   └── managers.py      # TenantScopedQuerySet & Manager
│   ├── topics/
│   │   ├── models.py        # Topic (context, JSON schema, failure_policy)
│   │   ├── schemas.py       # Pydantic v2 Topic CRUD schemas
│   │   ├── api.py           # /topics endpoints
│   │   └── services.py      # Schema validation & version bumping
│   ├── ingestion/
│   │   ├── models.py        # Document, DocumentChunk (with pgvector HNSW)
│   │   ├── parsers.py       # File (PDF/DOCX/MD/HTML) & URL (SSRF-safe) parsers
│   │   ├── chunkers.py      # Header-aware recursive Markdown splitter
│   │   ├── tasks.py         # Celery chain: parse -> validate -> extract -> index
│   │   ├── schemas.py       # Upload, URL, and Document status schemas
│   │   └── api.py           # /topics/{id}/documents/* and /documents/{id}
│   ├── quarantine/
│   │   ├── models.py        # QuarantineReview (HITL queue)
│   │   ├── services.py      # Approve override, edit & index, reassign, reject
│   │   ├── schemas.py
│   │   └── api.py           # /quarantine endpoints
│   ├── retrieval/
│   │   ├── filters.py       # Safe JSONB MetadataFilter -> Django Q translator
│   │   ├── search.py        # Hybrid search (pgvector iterative_scan + FTS + RRF)
│   │   ├── synthesis.py     # Single-topic & multi-topic grounded synthesis + SSE
│   │   ├── schemas.py       # RetrieveRequest, SynthesizeRequest, Responses
│   │   └── api.py           # /rag/retrieve and /rag/synthesize
│   └── llm_gateway/
│       ├── models.py        # LLMUsageLog
│       ├── client.py        # LiteLLM wrapper (retries, fallbacks, BYOK lookup)
│       └── prompts.py       # Semantic gatekeeper, extraction, and synthesis prompts
├── manage.py
└── spec.md
```

---

## 4. Data Models & `pgvector` Schema

Ensure the PostgreSQL `vector` extension is enabled via a migration (`VectorExtension()` from `pgvector.django`) before creating `DocumentChunk`.

### 4.1 Tenancy & Scoped Manager (`apps/tenancy/`)

```python
import uuid
from django.db import models


class TenantScopedQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantScopedManager(models.Manager):
    def get_queryset(self):
        return TenantScopedQuerySet(self.model, using=self._db)

    def for_tenant(self, tenant):
        return self.get_queryset().for_tenant(tenant)


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    default_synthesis_prompt = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)


class TenantProviderConfig(models.Model):
    """BYOK overrides for LiteLLM per tenant."""
    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="provider_config")
    default_extraction_model = models.CharField(max_length=128, blank=True, default="")
    default_synthesis_model = models.CharField(max_length=128, blank=True, default="")
    # Store encrypted JSON map of provider -> API key
    encrypted_api_keys = models.TextField(blank=True, default="")
```

### 4.2 Topic (`apps/topics/models.py`)

```python
import uuid
from django.db import models
from apps.tenancy.managers import TenantScopedManager


class Topic(models.Model):
    class FailurePolicy(models.TextChoices):
        STRICT_REJECT = "STRICT_REJECT", "Strict Reject"
        QUARANTINE = "QUARANTINE", "Quarantine for Review"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, related_name="topics")
    slug = models.SlugField(max_length=128)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    # Semantic Context
    system_description = models.TextField()
    glossary = models.JSONField(default=dict, blank=True)
    semantic_rules = models.JSONField(default=dict, blank=True)  # {"include": [...], "exclude": [...]}
    min_semantic_score = models.FloatField(default=0.75)

    # Structured Metadata Contract (JSON Schema Draft 2020-12)
    metadata_schema = models.JSONField(default=dict)
    schema_version = models.PositiveIntegerField(default=1)

    # Pipeline & Synthesis Policy
    failure_policy = models.CharField(
        max_length=32, choices=FailurePolicy.choices, default=FailurePolicy.QUARANTINE
    )
    chunking_config = models.JSONField(
        default=dict  # {"chunk_size": 800, "chunk_overlap": 120, "strategy": "markdown_recursive"}
    )
    synthesis_prompt = models.TextField(blank=True, default="")
    extraction_model = models.CharField(max_length=128, blank=True, null=True)
    synthesis_model = models.CharField(max_length=128, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        unique_together = [("tenant", "slug")]
```

### 4.3 Document & DocumentChunk (`apps/ingestion/models.py`)

```python
import uuid
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from pgvector.django import HnswIndex, VectorField
from apps.tenancy.managers import TenantScopedManager


class Document(models.Model):
    class SourceType(models.TextChoices):
        FILE = "FILE", "File Upload"
        URL = "URL", "Web URL"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PARSING = "PARSING", "Parsing"
        VALIDATING = "VALIDATING", "Validating"
        QUARANTINED = "QUARANTINED", "Quarantined"
        REJECTED = "REJECTED", "Rejected"
        INDEXING = "INDEXING", "Indexing"
        INDEXED = "INDEXED", "Indexed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, db_index=True)
    topic = models.ForeignKey("topics.Topic", on_delete=models.CASCADE, related_name="documents")

    source_type = models.CharField(max_length=16, choices=SourceType.choices)
    source_uri = models.TextField()
    raw_storage_key = models.CharField(max_length=512, blank=True, default="")
    normalized_storage_key = models.CharField(max_length=512, blank=True, default="")
    content_hash = models.CharField(max_length=64, db_index=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    schema_version_applied = models.PositiveIntegerField(default=1)
    semantic_score = models.FloatField(null=True, blank=True)
    extracted_metadata = models.JSONField(default=dict, blank=True)
    validation_report = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "topic", "content_hash"]),
            models.Index(fields=["tenant", "status"]),
        ]


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE, db_index=True)
    topic = models.ForeignKey("topics.Topic", on_delete=models.CASCADE, db_index=True)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")

    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    token_count = models.PositiveIntegerField()

    # Locked to system-wide uniform default embedding model dimension
    embedding = VectorField(dimensions=1536)
    search_vector = SearchVectorField(null=True)
    metadata = models.JSONField(default=dict)  # Denormalized Document.extracted_metadata + chunk anchors

    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "topic"], name="chunk_tenant_topic_idx"),
            GinIndex(fields=["metadata"], name="chunk_metadata_gin", opclasses=["jsonb_path_ops"]),
            GinIndex(fields=["search_vector"], name="chunk_fts_gin"),
            HnswIndex(
                name="chunk_embedding_hnsw",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]
```

### 4.4 Quarantine & Usage Logging (`apps/quarantine/models.py` & `apps/llm_gateway/models.py`)

* **`QuarantineReview`**: `id` (UUID), `tenant` (FK), `document` (OneToOneField), `failure_type` (`SEMANTIC_MISMATCH` | `SCHEMA_VALIDATION` | `BOTH`), `suggested_topics` (JSONB), `proposed_metadata` (JSONB), `resolution` (`PENDING` | `APPROVED_OVERRIDE` | `EDITED_AND_INDEXED` | `REASSIGNED` | `REJECTED`), `resolution_notes` (TextField), `resolved_at` (DateTimeField, nullable).
* **`LLMUsageLog`**: `id` (UUID), `tenant` (FK), `topic` (FK, nullable), `operation` (`SEMANTIC_GATE` | `EXTRACTION` | `EMBEDDING` | `SYNTHESIS` | `RERANK`), `model` (CharField), `prompt_tokens` (Int), `completion_tokens` (Int), `estimated_cost_usd` (DecimalField), `created_at` (DateTimeField).

---

## 5. Context-Enforced Ingestion Pipeline (Celery + Redis)

Configure three dedicated Celery queues in `config/celery.py`:
* `parsing`: CPU/IO-bound document extraction and URL fetching.
* `llm_io`: Network-bound LiteLLM semantic gatekeeping and structured extraction.
* `indexing`: Batch embedding and atomic PostgreSQL writes.

### Celery Task Chain (`apps/ingestion/tasks.py`)

```python
from celery import chain

def dispatch_ingestion_pipeline(document_id: str) -> None:
    workflow = chain(
        parse_document_task.s(document_id).set(queue="parsing"),
        validate_and_extract_task.s().set(queue="llm_io"),
        index_document_task.s().set(queue="indexing"),
    )
    workflow.apply_async()
```

### Stage Specifications

1. **Intake (HTTP Sync):**
   * Verify `topic` belongs to `request.tenant`.
   * Store raw file (or URL metadata) in S3, compute SHA-256 `content_hash`.
   * If an `INDEXED` document with the same `content_hash` already exists in that `topic`, return `200 OK` with existing `document_id` (unless `force_reindex=true`).
   * Create `Document(status=PENDING)` and dispatch Celery chain; return `202 Accepted`.

2. **Stage 1 — `parse_document_task` (`parsing` queue):**
   * Set `status = PARSING`.
   * If `URL`: run SSRF validation (resolve DNS, block loopback, RFC1918 private ranges, link-local `169.254.169.254`, and non-HTTP schemes) before fetching with `trafilatura`.
   * If `FILE`: extract normalized Markdown with section/page markers using `markitdown` / `docling`.
   * Persist normalized Markdown to S3 (`normalized_storage_key`) and return `document_id`.

3. **Stage 2 — `validate_and_extract_task` (`llm_io` queue):**
   * Set `status = VALIDATING`.
   * **Semantic Gatekeeping:** Call LiteLLM with structured output (`{is_on_topic: bool, confidence_score: float, rationale: str}`) using `Topic.system_description`, `glossary`, and `semantic_rules`.
   * **LLM-Assisted Extraction:** Call LiteLLM with `Topic.metadata_schema` as the JSON Schema target. Validate output strictly with `jsonschema.validate()`.
   * **Policy Gate:**
     * If **both pass**: Save `extracted_metadata`, `semantic_score`, and `validation_report`; return `document_id` to proceed to Stage 3.
     * If **either fails** and `Topic.failure_policy == STRICT_REJECT`: Set `status = REJECTED`, save `validation_report`, and return `None` (halts chain).
     * If **either fails** and `Topic.failure_policy == QUARANTINE`: Set `status = QUARANTINED`, create `QuarantineReview`, and return `None` (halts chain).

4. **Stage 3 — `index_document_task` (`indexing` queue):**
   * If `document_id is None`, exit immediately.
   * Set `status = INDEXING`.
   * Split normalized Markdown using `Topic.chunking_config`.
   * Batch-embed chunks via `litellm.embedding()` using the **uniform system-wide default embedding model**.
   * Inside `transaction.atomic()`:
     * Delete existing chunks for `document_id`.
     * `DocumentChunk.objects.bulk_create(...)` with denormalized `metadata = {**doc.extracted_metadata, "page": ..., "heading": ...}`.
     * Populate `search_vector = SearchVector("content", config="english")`.
     * Set `Document.status = INDEXED`.

---

## 6. Hybrid Retrieval & Synthesis Engine

### 6.1 Hybrid Search (`apps/retrieval/search.py`)

1. **Tenant & Topic Scope:** Verify all `topic_ids` belong to `request.tenant`.
2. **Metadata Filtering (`apps/retrieval/filters.py`):** Translate `list[MetadataFilter]` into parameterized Django `Q` objects against `DocumentChunk.metadata` (`metadata__<field>`, `metadata__<field>__in`, `metadata__<field>__gte`, etc.). Never interpolate raw SQL.
3. **Dense + Sparse Execution (Single Transaction):**
   * Open `with transaction.atomic():`
   * Execute `cursor.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order';")` so `pgvector` 0.8+ satisfies `top_k` even under selective tenant/topic/JSONB filters.
   * **Dense Top-N:** `DocumentChunk.objects.for_tenant(tenant).filter(topic_id__in=topic_ids).filter(metadata_q).annotate(distance=CosineDistance("embedding", query_vec)).order_by("distance")[:top_k * 3]`
   * **Sparse Top-N:** Same tenant/topic/metadata filter with `SearchQuery(query, search_type="websearch")` and `SearchRank("search_vector", sq)`.
4. **Reciprocal Rank Fusion (RRF):**
   $$\text{score}(d) = \frac{\alpha}{k + \text{rank}_{\text{dense}}(d)} + \frac{1 - \alpha}{k + \text{rank}_{\text{sparse}}(d)}$$
   with $k = 60$ and $\alpha = \text{hybrid\_alpha}$.
5. **Optional Reranking:** If `rerank=True`, pass top candidates through `litellm.rerank()`.

### 6.2 Grounded Synthesis (`apps/retrieval/synthesis.py`)

* **Single-Topic Mode:** Use `Topic.synthesis_prompt`, `system_description`, and `glossary`.
* **Multi-Topic Mode:** Use `Tenant.default_synthesis_prompt`, merge relevant glossaries, and prefix every retrieved chunk with `[Topic: <topic_name> | Source: <source_uri> | Chunk: <chunk_id>]` so the LLM attributes facts accurately and flags cross-topic conflicts.
* **Streaming:** Support `stream=True` via Django `StreamingHttpResponse` emitting Server-Sent Events (`text/event-stream`).

---

## 7. API Surface (Django Ninja — `/api/v1/`)

| Group | Method & Path | Description |
| :--- | :--- | :--- |
| **Topics** | `POST /topics` | Create a Topic (validates `metadata_schema` is valid JSON Schema). |
| | `GET /topics` | List active Topics for the authenticated tenant. |
| | `PATCH /topics/{topic_id}` | Update Topic context; auto-increments `schema_version` if `metadata_schema` changes. |
| **Ingestion** | `POST /topics/{topic_id}/documents/upload` | Multipart file upload -> `202 Accepted` (`document_id`). |
| | `POST /topics/{topic_id}/documents/url` | JSON `{url, force_reindex}` -> `202 Accepted` (`document_id`). |
| | `GET /documents/{document_id}` | Poll ingestion status, `extracted_metadata`, and `validation_report`. |
| | `DELETE /documents/{document_id}` | Hard-delete document, chunks, and S3 objects. |
| **Quarantine** | `GET /quarantine` | List `PENDING` quarantine reviews for the tenant. |
| | `POST /quarantine/{review_id}/resolve` | Resolve via `APPROVED_OVERRIDE`, `EDITED_AND_INDEXED`, `REASSIGNED`, or `REJECTED`. |
| **RAG** | `POST /rag/retrieve` | Hybrid retrieval returning ranked `RetrievedChunkOut` list. |
| | `POST /rag/synthesize` | Hybrid retrieval + grounded LLM answer (JSON or SSE stream). |

---

## 8. Security & Operational Guardrails

* **Strict Multi-Tenancy:** Every query must use `.for_tenant(request.tenant)`. Add a test suite asserting zero cross-tenant leakage across Topics, Documents, Quarantine, and RAG endpoints.
* **Uniform Embedding Lock:** Define `DEFAULT_EMBEDDING_MODEL` and `DEFAULT_EMBEDDING_DIMENSIONS = 1536` in `settings.py`. Changing the model requires running a zero-downtime re-embedding management command (`manage.py reindex_embeddings`).
* **LiteLLM Resilience:** Configure `autoretry_for=(RateLimitError, APIConnectionError, Timeout)` with exponential backoff on Celery `llm_io` tasks, and log every call to `LLMUsageLog`.

---

## 9. Phased Implementation Plan (Antigravity IDE Checklist)

1. **Phase 1 — Foundation & Schema:** Initialize Django project, configure PostgreSQL + `pgvector` extension migration, implement `tenancy`, `topics`, `ingestion`, `quarantine`, and `llm_gateway` models, and run migrations.
2. **Phase 2 — API & Auth:** Implement Django Ninja `TenantAuth`, Topic CRUD endpoints (with JSON Schema validation), and Document intake endpoints.
3. **Phase 3 — Ingestion Pipeline:** Implement SSRF-safe URL/file parsers, Markdown chunker, LiteLLM gateway client, and the 3-stage Celery task chain (`parse` -> `validate_and_extract` -> `index`).
4. **Phase 4 — Quarantine HITL:** Implement Quarantine listing and resolution service (including re-dispatching Stage 3 on edit/override or Stage 2 on Topic reassignment).
5. **Phase 5 — Hybrid Retrieval & Synthesis:** Implement JSONB filter translator, `pgvector` `iterative_scan` + FTS + RRF search engine, and `/rag/retrieve` + `/rag/synthesize` (with SSE streaming).
6. **Phase 6 — Verification Suite:** Write `pytest-django` tests covering tenant isolation, `STRICT_REJECT` vs. `QUARANTINE` flows, filtered hybrid search accuracy, and multi-topic synthesis citations.