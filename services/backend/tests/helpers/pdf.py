"""Hand-written minimal PDF fixtures (task-17-brief.md).

``pypdf`` can read PDFs but not author one from scratch with real text
content, so the smallest valid single-page PDFs used by the retrieval
tests are built here directly from PDF syntax (objects, xref table,
trailer) with correctly computed byte offsets. ``pypdf.PdfWriter.encrypt``
is used afterwards to produce the encrypted fixture from the plain one.
"""

from __future__ import annotations

from io import BytesIO


def _build_pdf(content_stream: bytes) -> bytes:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(content_stream)).encode("ascii")
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream",
    ]

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets: list[int] = [0]  # object 0 is the free-list head, never used here
    for index, body in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{index} 0 obj\n".encode("ascii"))
        buffer.write(body)
        buffer.write(b"\nendobj\n")

    xref_offset = buffer.tell()
    count = len(objects) + 1
    buffer.write(f"xref\n0 {count}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return buffer.getvalue()


def make_text_pdf(text: str = "Hello LineSense retrieval pipeline") -> bytes:
    """A valid one-page PDF whose page renders ``text`` (ASCII, no parens/backslashes)."""
    if "(" in text or ")" in text or "\\" in text:
        raise ValueError("fixture text must not contain '(', ')' or '\\'")
    stream = f"BT /F1 18 Tf 72 700 Td ({text}) Tj ET".encode("ascii")
    return _build_pdf(stream)


def make_no_text_pdf() -> bytes:
    """A valid one-page PDF with an empty content stream (no text objects at all)."""
    return _build_pdf(b"q Q")


def make_encrypted_pdf(
    text: str = "Secret LineSense content",
    password: str = "test-password",  # noqa: S107 - test fixture, not a real credential
) -> bytes:
    """The text PDF above, re-saved encrypted with ``pypdf.PdfWriter.encrypt``."""
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415 - test-only, mirrors app lazy imports

    reader = PdfReader(BytesIO(make_text_pdf(text)))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
