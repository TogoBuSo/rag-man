from __future__ import annotations

from uuid import UUID

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connection, transaction
from ninja.errors import HttpError
from pgvector.django import CosineDistance

from apps.ingestion.models import DocumentChunk
from apps.llm_gateway.client import llm_gateway
from apps.retrieval.filters import build_metadata_q
from apps.retrieval.schemas import RetrievedChunkOut, RetrieveRequest
from apps.tenancy.models import Tenant
from apps.topics.models import Topic

RRF_K = 60


def hybrid_search(
    tenant: Tenant, request_data: RetrieveRequest
) -> tuple[list[Topic], list[RetrievedChunkOut]]:
    unique_topic_ids = list(dict.fromkeys(request_data.topic_ids))
    topics = list(
        Topic.objects.for_tenant(tenant).filter(id__in=unique_topic_ids, is_active=True)
    )
    if len(topics) != len(unique_topic_ids):
        raise HttpError(404, "One or more requested topics were not found for this tenant.")

    metadata_q = build_metadata_q(request_data.metadata_filters)
    primary_topic = topics[0] if len(topics) == 1 else None
    query_vec = llm_gateway.embed_texts(
        tenant=tenant,
        topic=primary_topic,
        texts=[request_data.query],
    )[0]

    candidate_limit = request_data.top_k * 3
    search_query = SearchQuery(request_data.query, search_type="websearch", config="english")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order';")

        base_qs = (
            DocumentChunk.objects.for_tenant(tenant)
            .filter(topic_id__in=unique_topic_ids)
            .filter(metadata_q)
            .select_related("topic", "document")
        )

        dense_chunks = list(
            base_qs.annotate(distance=CosineDistance("embedding", query_vec))
            .order_by("distance")[:candidate_limit]
        )
        sparse_chunks = list(
            base_qs.annotate(rank=SearchRank("search_vector", search_query))
            .filter(rank__gt=0)
            .order_by("-rank")[:candidate_limit]
        )

    chunk_map: dict[UUID, DocumentChunk] = {}
    dense_ranks: dict[UUID, int] = {}
    sparse_ranks: dict[UUID, int] = {}

    for rank_idx, chunk in enumerate(dense_chunks, start=1):
        chunk_map[chunk.id] = chunk
        dense_ranks[chunk.id] = rank_idx

    for rank_idx, chunk in enumerate(sparse_chunks, start=1):
        chunk_map[chunk.id] = chunk
        sparse_ranks[chunk.id] = rank_idx

    alpha = request_data.hybrid_alpha
    scored: list[tuple[UUID, float]] = []
    for cid in chunk_map:
        dense_term = (alpha / (RRF_K + dense_ranks[cid])) if cid in dense_ranks else 0.0
        sparse_term = ((1.0 - alpha) / (RRF_K + sparse_ranks[cid])) if cid in sparse_ranks else 0.0
        scored.append((cid, dense_term + sparse_term))

    scored.sort(key=lambda item: item[1], reverse=True)

    if request_data.rerank and scored:
        top_candidates = scored[: max(request_data.top_k * 2, request_data.top_k)]
        candidate_chunks = [chunk_map[cid] for cid, _ in top_candidates]
        reranked = llm_gateway.rerank(
            tenant=tenant,
            topic=primary_topic,
            query=request_data.query,
            documents=[c.content for c in candidate_chunks],
            top_n=request_data.top_k,
        )
        final_scored = [
            (candidate_chunks[idx].id, rel_score)
            for idx, rel_score in reranked
            if 0 <= idx < len(candidate_chunks)
        ]
    else:
        final_scored = scored[: request_data.top_k]

    results = [
        RetrievedChunkOut(
            chunk_id=chunk_map[cid].id,
            document_id=chunk_map[cid].document_id,
            topic_id=chunk_map[cid].topic_id,
            topic_name=chunk_map[cid].topic.name,
            source_uri=chunk_map[cid].document.source_uri,
            chunk_index=chunk_map[cid].chunk_index,
            content=chunk_map[cid].content,
            metadata=chunk_map[cid].metadata,
            score=round(score, 6),
            dense_rank=dense_ranks.get(cid),
            sparse_rank=sparse_ranks.get(cid),
        )
        for cid, score in final_scored
    ]
    return topics, results
