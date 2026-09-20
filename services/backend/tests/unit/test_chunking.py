"""Unit tests for `app.retrieval.chunking` (task-17-brief.md)."""

from __future__ import annotations

from app.retrieval.chunking import ChunkDraft, chunk_sections
from app.retrieval.extract import ExtractedSection


def _words(n: int, prefix: str = "word") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_short_section_becomes_a_single_chunk() -> None:
    sections = [ExtractedSection(page_number=1, section="Intro", text=_words(50))]
    chunks = chunk_sections(sections)
    assert len(chunks) == 1
    assert chunks[0] == ChunkDraft(
        chunk_index=0, page_number=1, section="Intro", text=_words(50), token_count=50
    )


def test_long_section_is_split_with_overlap() -> None:
    sections = [ExtractedSection(page_number=None, section="Body", text=_words(1200))]
    chunks = chunk_sections(sections, target_tokens=500, overlap_tokens=80, max_tokens=700)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.token_count <= 700

    # Overlap: the tail of one chunk reappears at the head of the next.
    first_words = chunks[0].text.split()
    second_words = chunks[1].text.split()
    assert first_words[-80:] == second_words[:80]


def test_chunk_never_exceeds_max_tokens() -> None:
    sections = [ExtractedSection(page_number=1, section=None, text=_words(701))]
    chunks = chunk_sections(sections, target_tokens=500, overlap_tokens=80, max_tokens=700)
    assert all(chunk.token_count <= 500 for chunk in chunks[:-1])
    assert all(chunk.token_count <= 700 for chunk in chunks)


def test_chunks_never_cross_section_boundaries() -> None:
    sections = [
        ExtractedSection(page_number=1, section="A", text=_words(600, "a")),
        ExtractedSection(page_number=2, section="B", text=_words(10, "b")),
    ]
    chunks = chunk_sections(sections, target_tokens=500, overlap_tokens=80, max_tokens=700)
    # Section A (600 words, exceeds max 700? no -> single chunk) then section B.
    for chunk in chunks:
        words = chunk.text.split()
        assert all(w.startswith("a") for w in words) or all(w.startswith("b") for w in words)


def test_chunk_indices_are_stable_and_sequential() -> None:
    sections = [
        ExtractedSection(page_number=1, section="A", text=_words(1100, "a")),
        ExtractedSection(page_number=2, section="B", text=_words(30, "b")),
    ]
    chunks = chunk_sections(sections)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    # Re-running with the same input produces identical output (determinism).
    again = chunk_sections(sections)
    assert chunks == again


def test_empty_section_text_is_skipped() -> None:
    sections = [
        ExtractedSection(page_number=1, section="Empty", text=""),
        ExtractedSection(page_number=2, section="Real", text=_words(5)),
    ]
    chunks = chunk_sections(sections)
    assert len(chunks) == 1
    assert chunks[0].section == "Real"
