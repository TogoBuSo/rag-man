from __future__ import annotations

import pytest
from ninja.errors import HttpError

from apps.ingestion.chunkers import chunk_markdown
from apps.retrieval.filters import build_metadata_q
from apps.retrieval.schemas import MetadataFilter
from config.urls import api


def test_openapi_schema_exposes_all_spec_endpoints() -> None:
    schema = api.get_openapi_schema()
    paths = set(schema["paths"].keys())
    expected = {
        "/api/v1/topics",
        "/api/v1/topics/{topic_id}",
        "/api/v1/topics/{topic_id}/documents/upload",
        "/api/v1/topics/{topic_id}/documents/url",
        "/api/v1/documents/{document_id}",
        "/api/v1/quarantine",
        "/api/v1/quarantine/{review_id}/resolve",
        "/api/v1/rag/retrieve",
        "/api/v1/rag/synthesize",
    }
    assert expected.issubset(paths)


def test_markdown_chunker_preserves_headings_pages_and_overlap() -> None:
    long_para = " ".join(f"word{i}" for i in range(130))
    md = (
        "<!-- page: 1 -->\n"
        "# Overview\n"
        "Short intro paragraph.\n\n"
        "<!-- page: 2 -->\n"
        "## Deep Dive\n"
        f"{long_para}\n"
    )
    chunks = chunk_markdown(md, {"chunk_size": 60, "chunk_overlap": 10})
    assert len(chunks) >= 3
    assert chunks[0].heading == "Overview"
    assert chunks[0].page == 1
    assert chunks[1].heading == "Deep Dive"
    assert chunks[1].page == 2
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_metadata_filter_q_translation_and_injection_guard() -> None:
    q = build_metadata_q(
        [
            MetadataFilter(field="department", operator="eq", value="security"),
            MetadataFilter(field="year", operator="gte", value=2025),
            MetadataFilter(field="tags", operator="contains", value=["soc2"]),
            MetadataFilter(field="region", operator="in", value=["us", "eu"]),
            MetadataFilter(field="nested.tier", operator="ne", value="deprecated"),
        ]
    )
    assert q is not None

    with pytest.raises(HttpError) as exc_info:
        build_metadata_q([MetadataFilter(field="year; DROP TABLE", operator="eq", value=2026)])
    assert exc_info.value.status_code == 422

    with pytest.raises(HttpError) as exc_info2:
        build_metadata_q([MetadataFilter(field="region", operator="in", value="not-a-list")])
    assert exc_info2.value.status_code == 422
