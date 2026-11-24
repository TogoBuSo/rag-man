from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from apps.ingestion.models import DocumentChunk
from apps.llm_gateway.client import llm_gateway


class Command(BaseCommand):
    help = "Re-embed DocumentChunk records in batches using the current DEFAULT_EMBEDDING_MODEL."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant-slug", type=str, default=None, help="Limit to a single tenant slug")
        parser.add_argument("--topic-slug", type=str, default=None, help="Limit to a single topic slug")
        parser.add_argument("--batch-size", type=int, default=64, help="Embedding batch size")

    def handle(self, *args: Any, **options: Any) -> None:
        batch_size = max(1, int(options["batch_size"]))
        qs = DocumentChunk.objects.select_related("tenant", "topic").order_by("tenant_id", "topic_id", "id")

        if options["tenant_slug"]:
            qs = qs.filter(tenant__slug=options["tenant_slug"])
        if options["topic_slug"]:
            qs = qs.filter(topic__slug=options["topic_slug"])

        chunks = list(qs)
        total = len(chunks)
        updated = 0

        for start in range(0, total, batch_size):
            batch = chunks[start : start + batch_size]
            # Group by (tenant_id, topic_id) for accurate LLMUsageLog attribution
            groups: dict[tuple[Any, Any], list[DocumentChunk]] = {}
            for chunk in batch:
                groups.setdefault((chunk.tenant_id, chunk.topic_id), []).append(chunk)

            for group_chunks in groups.values():
                tenant = group_chunks[0].tenant
                topic = group_chunks[0].topic
                vectors = llm_gateway.embed_texts(
                    tenant=tenant,
                    topic=topic,
                    texts=[c.content for c in group_chunks],
                )
                for c, vec in zip(group_chunks, vectors, strict=True):
                    c.embedding = vec
                with transaction.atomic():
                    DocumentChunk.objects.bulk_update(group_chunks, ["embedding"])
                updated += len(group_chunks)

        self.stdout.write(self.style.SUCCESS(f"Re-embedded {updated}/{total} document chunks."))
