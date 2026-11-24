from __future__ import annotations

from django.http import HttpRequest, StreamingHttpResponse
from ninja import Router

from apps.retrieval.schemas import (
    RetrievedChunkOut,
    RetrieveRequest,
    SynthesizeRequest,
    SynthesizeResponse,
)
from apps.retrieval.search import hybrid_search
from apps.retrieval.synthesis import synthesize_answer

router = Router()


@router.post("/rag/retrieve", response=list[RetrievedChunkOut])
def retrieve_endpoint(
    request: HttpRequest, payload: RetrieveRequest
) -> list[RetrievedChunkOut]:
    _, results = hybrid_search(request.tenant, payload)  # type: ignore[attr-defined]
    return results


@router.post("/rag/synthesize", response=SynthesizeResponse)
def synthesize_endpoint(
    request: HttpRequest, payload: SynthesizeRequest
) -> SynthesizeResponse | StreamingHttpResponse:
    return synthesize_answer(request.tenant, payload)  # type: ignore[attr-defined]
