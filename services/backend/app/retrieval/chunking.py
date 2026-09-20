"""Chunking of extracted document sections (task-17-brief.md req. 2).

"Tokens" are approximated as whitespace-separated words, matching the
brief exactly (no tokenizer dependency for this bookkeeping). A chunk never
spans two :class:`~app.retrieval.extract.ExtractedSection` entries — each
extracted section (one PDF page, or one markdown heading block) is chunked
independently — so ``page_number``/``section`` are never ambiguous for a
chunk. Chunk indices are assigned sequentially across the whole document,
so they are stable as long as extraction is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.extract import ExtractedSection

DEFAULT_TARGET_TOKENS = 500
DEFAULT_OVERLAP_TOKENS = 80
DEFAULT_MAX_TOKENS = 700


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    page_number: int | None
    section: str | None
    text: str
    token_count: int


def chunk_sections(
    sections: list[ExtractedSection],
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[ChunkDraft]:
    if target_tokens <= 0 or max_tokens <= 0:
        raise ValueError("target_tokens and max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")

    drafts: list[ChunkDraft] = []
    index = 0
    for section in sections:
        words = section.text.split()
        if not words:
            continue
        for chunk_words in _windows(
            words, target_tokens=target_tokens, overlap_tokens=overlap_tokens, max_tokens=max_tokens
        ):
            drafts.append(
                ChunkDraft(
                    chunk_index=index,
                    page_number=section.page_number,
                    section=section.section,
                    text=" ".join(chunk_words),
                    token_count=len(chunk_words),
                )
            )
            index += 1
    return drafts


def _windows(
    words: list[str], *, target_tokens: int, overlap_tokens: int, max_tokens: int
) -> list[list[str]]:
    total = len(words)
    if total <= max_tokens:
        return [words]

    step = target_tokens - overlap_tokens
    windows: list[list[str]] = []
    start = 0
    while start < total:
        end = min(start + target_tokens, total)
        windows.append(words[start:end])
        if end >= total:
            break
        start += step
    return windows
