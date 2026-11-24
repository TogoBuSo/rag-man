from __future__ import annotations

import json
from typing import Any

from apps.tenancy.models import Tenant
from apps.topics.models import Topic

MAX_PROMPT_DOC_CHARS = 24000


def build_semantic_gate_messages(topic: Topic, markdown_text: str) -> list[dict[str, str]]:
    truncated = markdown_text[:MAX_PROMPT_DOC_CHARS]
    system_prompt = (
        "You are a strict semantic gatekeeper for a multi-tenant knowledge base.\n"
        "Evaluate whether the provided document belongs to the target Topic based on its "
        "description, glossary, and inclusion/exclusion rules.\n"
        "Respond ONLY with a valid JSON object matching this exact structure:\n"
        '{"is_on_topic": boolean, "confidence_score": number_between_0_and_1, "rationale": string}'
    )
    user_prompt = (
        f"Topic Name: {topic.name}\n"
        f"System Description: {topic.system_description}\n"
        f"Glossary: {json.dumps(topic.glossary)}\n"
        f"Semantic Rules: {json.dumps(topic.semantic_rules)}\n"
        f"Minimum Semantic Score Threshold: {topic.min_semantic_score}\n\n"
        f"Document Content:\n{truncated}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_topic_suggestion_messages(
    candidate_topics: list[Topic], markdown_text: str
) -> list[dict[str, str]]:
    truncated = markdown_text[:12000]
    topic_summaries = [
        {
            "topic_id": str(t.id),
            "slug": t.slug,
            "name": t.name,
            "system_description": t.system_description,
            "semantic_rules": t.semantic_rules,
        }
        for t in candidate_topics
    ]
    system_prompt = (
        "You classify a rejected document against alternative Topics within the same tenant.\n"
        "Return up to 3 best-matching candidate topics as JSON:\n"
        '{"suggested_topics": [{"topic_id": string, "slug": string, "confidence": number, "reason": string}]}'
    )
    user_prompt = (
        f"Candidate Topics:\n{json.dumps(topic_summaries, indent=2)}\n\n"
        f"Document Content:\n{truncated}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_extraction_messages(topic: Topic, markdown_text: str) -> list[dict[str, str]]:
    truncated = markdown_text[:MAX_PROMPT_DOC_CHARS]
    system_prompt = (
        "You are a structured metadata extractor.\n"
        "Extract metadata from the document that strictly conforms to the provided JSON Schema "
        "(Draft 2020-12). Return ONLY valid JSON with no markdown fences or commentary."
    )
    user_prompt = (
        f"Topic: {topic.name}\n"
        f"Glossary: {json.dumps(topic.glossary)}\n"
        f"Target JSON Schema:\n{json.dumps(topic.metadata_schema, indent=2)}\n\n"
        f"Document Content:\n{truncated}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_synthesis_messages(
    query: str,
    chunks: list[dict[str, Any]],
    topics: list[Topic],
    tenant: Tenant,
) -> list[dict[str, str]]:
    if len(topics) == 1:
        topic = topics[0]
        base_prompt = topic.synthesis_prompt or (
            "You are a grounded assistant. Answer the user's question using ONLY the provided context chunks. "
            "Cite chunk IDs inline and state clearly if the context does not contain enough information."
        )
        system_prompt = (
            f"{base_prompt}\n\n"
            f"Topic Context: {topic.system_description}\n"
            f"Glossary: {json.dumps(topic.glossary)}"
        )
        context_lines = [
            f"[Source: {c['source_uri']} | Chunk: {c['chunk_id']}]\n{c['content']}"
            for c in chunks
        ]
    else:
        base_prompt = tenant.default_synthesis_prompt or (
            "You are a grounded multi-topic assistant. Answer the user's question using ONLY the provided context. "
            "Attribute every fact to its Topic, Source, and Chunk ID, and explicitly flag any cross-topic conflicts."
        )
        merged_glossary: dict[str, Any] = {}
        for t in topics:
            merged_glossary.update(t.glossary)
        system_prompt = (
            f"{base_prompt}\n\n"
            f"Merged Glossary: {json.dumps(merged_glossary)}"
        )
        context_lines = [
            f"[Topic: {c['topic_name']} | Source: {c['source_uri']} | Chunk: {c['chunk_id']}]\n{c['content']}"
            for c in chunks
        ]

    context_block = "\n\n---\n\n".join(context_lines) if context_lines else "(No matching context chunks found.)"
    user_prompt = f"Question:\n{query}\n\nRetrieved Context:\n{context_block}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
