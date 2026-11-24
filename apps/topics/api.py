from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.topics.schemas import TopicCreateIn, TopicOut, TopicUpdateIn
from apps.topics.services import create_topic, list_active_topics, update_topic

router = Router()


@router.post("/topics", response={201: TopicOut})
def create_topic_endpoint(request: HttpRequest, payload: TopicCreateIn) -> tuple[int, TopicOut]:
    topic = create_topic(request.tenant, payload)  # type: ignore[attr-defined]
    return 201, TopicOut.model_validate(topic, from_attributes=True)


@router.get("/topics", response=list[TopicOut])
def list_topics_endpoint(request: HttpRequest) -> list[TopicOut]:
    topics = list_active_topics(request.tenant)  # type: ignore[attr-defined]
    return [TopicOut.model_validate(t, from_attributes=True) for t in topics]


@router.patch("/topics/{topic_id}", response=TopicOut)
def update_topic_endpoint(
    request: HttpRequest, topic_id: UUID, payload: TopicUpdateIn
) -> TopicOut:
    topic = update_topic(request.tenant, topic_id, payload)  # type: ignore[attr-defined]
    return TopicOut.model_validate(topic, from_attributes=True)
