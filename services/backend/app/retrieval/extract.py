"""Structural scan and text extraction for uploaded documents (task-17-brief.md req. 2).

PDFs are rejected outright (never partially processed) when they are
encrypted, carry active-content markers (``/JavaScript``, ``/JS``,
``/Launch``, ``/EmbeddedFile``, ``/XFA``), exceed 200 pages, or yield no
page with at least 20 characters of extracted text (scanned/image-only —
this project has no OCR). Markdown/plain text has its YAML front matter
stripped, is split on heading lines into sections, and is control-character-
and whitespace-normalized.

This module does no I/O and no timeout handling; the caller
(``app.retrieval.pipeline``) runs it inside ``asyncio.wait_for(asyncio.
to_thread(...), timeout=30)`` so a pathological file cannot hang a worker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO

MAX_PDF_PAGES = 200
MIN_PAGE_TEXT_CHARS = 20
NO_TEXT_MESSAGE = "Scanned or image-only PDFs are not supported yet (no OCR)"

_ACTIVE_CONTENT_MARKERS: tuple[bytes, ...] = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/XFA",
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$", re.MULTILINE)
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_INLINE_WHITESPACE_RE = re.compile(r"[ \t]+")


class UnsupportedDocument(Exception):
    """The document cannot be processed (rejects the version, never a crash)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExtractedSection:
    page_number: int | None
    section: str | None
    text: str


def extract_text(data: bytes, media_type: str) -> list[ExtractedSection]:
    """Extract sections from ``data``. Raises :class:`UnsupportedDocument`."""
    if media_type == "application/pdf":
        return _extract_pdf(data)
    if media_type in ("text/markdown", "text/plain"):
        return _extract_markdown(data)
    raise UnsupportedDocument(f"Unsupported media type {media_type!r}")


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------


def _extract_pdf(data: bytes) -> list[ExtractedSection]:
    for marker in _ACTIVE_CONTENT_MARKERS:
        if marker in data:
            raise UnsupportedDocument(
                "PDF contains active-content features that are not supported."
            )

    from pypdf import PdfReader  # noqa: PLC0415 - kept lazy alongside fastembed's import style
    from pypdf.errors import PdfReadError  # noqa: PLC0415

    try:
        reader = PdfReader(BytesIO(data))
    except (PdfReadError, ValueError) as exc:
        raise UnsupportedDocument("The PDF could not be read.") from exc

    if reader.is_encrypted:
        raise UnsupportedDocument("Encrypted PDFs are not supported.")

    try:
        page_count = len(reader.pages)
    except (PdfReadError, ValueError) as exc:
        raise UnsupportedDocument("The PDF could not be read.") from exc
    if page_count > MAX_PDF_PAGES:
        raise UnsupportedDocument(f"PDF has more than {MAX_PDF_PAGES} pages.")

    sections: list[ExtractedSection] = []
    any_page_has_text = False
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - a single unreadable page never crashes the scan
            raw_text = ""
        if len(raw_text.strip()) >= MIN_PAGE_TEXT_CHARS:
            any_page_has_text = True
        cleaned = _normalize(raw_text)
        if cleaned:
            sections.append(ExtractedSection(page_number=index, section=None, text=cleaned))

    if not any_page_has_text:
        raise UnsupportedDocument(NO_TEXT_MESSAGE)
    return sections


# --------------------------------------------------------------------------
# Markdown / plain text
# --------------------------------------------------------------------------


def _strip_front_matter(text: str) -> str:
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    remainder = text[end + len("\n---") :]
    # Consume the rest of the closing fence's own line.
    newline_index = remainder.find("\n")
    return remainder[newline_index + 1 :] if newline_index != -1 else ""


def _extract_markdown(data: bytes) -> list[ExtractedSection]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedDocument("File is not valid UTF-8 text.") from exc
    if "\x00" in text:
        raise UnsupportedDocument("File contains NUL bytes.")

    body = _strip_front_matter(text)
    matches = list(_HEADING_RE.finditer(body))
    sections: list[ExtractedSection] = []

    preamble_end = matches[0].start() if matches else len(body)
    preamble = _normalize(body[:preamble_end])
    if preamble:
        sections.append(ExtractedSection(page_number=None, section=None, text=preamble))

    for position, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        chunk_text = _normalize(body[start:end])
        if chunk_text:
            sections.append(ExtractedSection(page_number=None, section=heading, text=chunk_text))

    return sections


def _normalize(text: str) -> str:
    """Remove control characters and collapse whitespace runs."""
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _INLINE_WHITESPACE_RE.sub(" ", text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()
