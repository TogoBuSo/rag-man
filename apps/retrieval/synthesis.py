from __future__ import annotations

import json
from collections.abc import Iterator

from django.http import StreamingHttpResponse

from apps.llm_gateway.client import llm_gateway
from apps.llm_gateway.models import LLMUsageLog
from apps.llm_gateway.prompts import build_synthesis_messages
from apps.retrieval.schemas import SynthesizeRequest, SynthesizeResponse
from apps.retrieval.search import hybrid_search
from apps.tenancy.models import Tenant


def synthesize_answer(
    tenant: Tenant, request_data: SynthesizeRequest
) -> SynthesizeResponse | StreamingHttpResponse:
    topics, citations = hybrid_search(tenant, request_data)
    primary_topic = topics[0] if len(topics) == 1 else None

    chunk_dicts = [
        {
            "chunk_id": str(c.chunk_id),
            "topic_name": c.topic_name,
            "source_uri": c.source_uri,
            "content": c.content,
        }
        for c in citations
    ]
    messages = build_synthesis_messages(
        query=request_data.query,
        chunks=chunk_dicts,
        topics=topics,
        tenant=tenant,
    )

    if request_data.stream:
        def event_stream() -> Iterator[str]:
            citations_payload = [c.model_dump(mode="json") for c in citations]
            yield f"data: {json.dumps({'type': 'citations', 'citations': citations_payload})}\n\n"
            for delta in llm_gateway.stream_text(
                tenant=tenant,
                topic=primary_topic,
                operation=LLMUsageLog.Operation.SYNTHESIS,
                messages=messages,
                role="synthesis",
            ):
                yield f"data: {json.dumps({'type': 'delta', 'content': delta})}\n\n"
            yield "data: [DONE]\n\n"

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        return response

    answer = llm_gateway.complete_text(
        tenant=tenant,
        topic=primary_topic,
        operation=LLMUsageLog.Operation.SYNTHESIS,
        messages=messages,
        role="synthesis",
    )
    return SynthesizeResponse(answer=answer, citations=citations)
