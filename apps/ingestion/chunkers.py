from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->", re.IGNORECASE)


@dataclass(frozen=True)
class ChunkData:
    chunk_index: int
    content: str
    token_count: int
    heading: str
    page: int | None


def estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))


def _split_text_with_overlap(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= chunk_size:
        return [" ".join(words)]

    step = max(1, chunk_size - min(chunk_overlap, chunk_size - 1))
    pieces: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + chunk_size)
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += step
    return pieces


def chunk_markdown(markdown_text: str, config: dict[str, Any] | None = None) -> list[ChunkData]:
    cfg = config or {}
    chunk_size = max(50, int(cfg.get("chunk_size", 800)))
    chunk_overlap = max(0, int(cfg.get("chunk_overlap", 120)))

    sections: list[tuple[str, int | None, list[str]]] = []
    current_heading = ""
    current_page: int | None = None
    current_lines: list[str] = []

    for raw_line in markdown_text.splitlines():
        page_match = PAGE_MARKER_RE.search(raw_line)
        if page_match:
            if current_lines:
                sections.append((current_heading, current_page, current_lines))
                current_lines = []
            current_page = int(page_match.group(1))
            continue

        heading_match = HEADING_RE.match(raw_line.strip())
        if heading_match:
            if current_lines:
                sections.append((current_heading, current_page, current_lines))
                current_lines = []
            current_heading = heading_match.group(2).strip()
            current_lines.append(raw_line)
        else:
            current_lines.append(raw_line)

    if current_lines:
        sections.append((current_heading, current_page, current_lines))

    chunks: list[ChunkData] = []
    idx = 0
    for heading, page, lines in sections:
        section_text = "\n".join(lines).strip()
        if not section_text:
            continue

        if estimate_tokens(section_text) <= chunk_size:
            chunks.append(
                ChunkData(
                    chunk_index=idx,
                    content=section_text,
                    token_count=estimate_tokens(section_text),
                    heading=heading,
                    page=page,
                )
            )
            idx += 1
            continue

        # Split by paragraphs first, then fall back to word-level overlap splitting
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section_text) if p.strip()]
        buffer = ""
        for para in paragraphs:
            candidate = f"{buffer}\n\n{para}".strip() if buffer else para
            if estimate_tokens(candidate) <= chunk_size:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(
                        ChunkData(
                            chunk_index=idx,
                            content=buffer,
                            token_count=estimate_tokens(buffer),
                            heading=heading,
                            page=page,
                        )
                    )
                    idx += 1
                    buffer = ""
                if estimate_tokens(para) <= chunk_size:
                    buffer = para
                else:
                    for sub in _split_text_with_overlap(para, chunk_size, chunk_overlap):
                        chunks.append(
                            ChunkData(
                                chunk_index=idx,
                                content=sub,
                                token_count=estimate_tokens(sub),
                                heading=heading,
                                page=page,
                            )
                        )
                        idx += 1
        if buffer:
            chunks.append(
                ChunkData(
                    chunk_index=idx,
                    content=buffer,
                    token_count=estimate_tokens(buffer),
                    heading=heading,
                    page=page,
                )
            )
            idx += 1

    return chunks
