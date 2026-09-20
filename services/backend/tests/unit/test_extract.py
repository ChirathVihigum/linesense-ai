"""Unit tests for `app.retrieval.extract` (task-17-brief.md)."""

from __future__ import annotations

import pytest

from app.retrieval.extract import UnsupportedDocument, extract_text
from tests.helpers.pdf import make_encrypted_pdf, make_no_text_pdf, make_text_pdf

MARKDOWN_WITH_FRONT_MATTER = """---
slug: test-doc
title: Test Doc
doc_type: SOP
scope: org
acl: []
version: 1
---
# Purpose and Scope

This is the purpose section.\x07 It has a control char above.

## Details

Some details here   with   extra   spaces.

## More Details

Final section text.
"""


def test_markdown_front_matter_is_stripped() -> None:
    sections = extract_text(MARKDOWN_WITH_FRONT_MATTER.encode("utf-8"), "text/markdown")
    joined = " ".join(section.text for section in sections)
    assert "slug:" not in joined
    assert "doc_type:" not in joined


def test_markdown_splits_into_heading_sections() -> None:
    sections = extract_text(MARKDOWN_WITH_FRONT_MATTER.encode("utf-8"), "text/markdown")
    names = [section.section for section in sections]
    assert names == ["Purpose and Scope", "Details", "More Details"]


def test_markdown_control_characters_and_whitespace_are_cleaned() -> None:
    sections = extract_text(MARKDOWN_WITH_FRONT_MATTER.encode("utf-8"), "text/markdown")
    purpose = next(s for s in sections if s.section == "Purpose and Scope")
    assert "\x07" not in purpose.text
    details = next(s for s in sections if s.section == "Details")
    assert "extra   spaces" not in details.text
    assert "extra spaces" in details.text


def test_plain_text_without_headings_is_one_section() -> None:
    sections = extract_text(b"Just a plain paragraph, no headings at all.", "text/plain")
    assert len(sections) == 1
    assert sections[0].section is None
    assert sections[0].page_number is None


def test_markdown_rejects_nul_bytes() -> None:
    with pytest.raises(UnsupportedDocument):
        extract_text(b"# Heading\n\x00null byte here", "text/markdown")


def test_markdown_rejects_invalid_utf8() -> None:
    with pytest.raises(UnsupportedDocument):
        extract_text(b"\xff\xfe# not utf-8", "text/markdown")


def test_minimal_text_pdf_extracts_page_one_text() -> None:
    data = make_text_pdf("Hello LineSense retrieval pipeline")
    sections = extract_text(data, "application/pdf")
    assert len(sections) == 1
    assert sections[0].page_number == 1
    assert "Hello LineSense retrieval pipeline" in sections[0].text


def test_encrypted_pdf_is_unsupported() -> None:
    data = make_encrypted_pdf()
    with pytest.raises(UnsupportedDocument):
        extract_text(data, "application/pdf")


def test_image_only_pdf_is_unsupported() -> None:
    data = make_no_text_pdf()
    with pytest.raises(UnsupportedDocument, match="Scanned or image-only"):
        extract_text(data, "application/pdf")


def test_pdf_with_javascript_marker_is_unsupported() -> None:
    data = make_text_pdf("Hello LineSense") + b"\n%/JavaScript marker appended for the test\n"
    with pytest.raises(UnsupportedDocument):
        extract_text(data, "application/pdf")


def test_unknown_media_type_is_unsupported() -> None:
    with pytest.raises(UnsupportedDocument):
        extract_text(b"whatever", "application/octet-stream")
