"""Load a directory of front-matter-tagged Markdown SOPs into the corpus.

Used by ``python -m app.seed --with-documents`` (task-17-brief.md req. 6).
Each file starts with a YAML front matter block::

    ---
    slug: material-reservation-policy
    title: Material Reservation Policy
    doc_type: SOP
    scope: org            # or a factory code, e.g. KTN / BYG
    acl: []               # list of role names, empty = readable by everyone
    version: 1            # informational only; the real version_no is assigned
    ---                   # sequentially by the pipeline, not read from here

A ``versions/`` subdirectory of ``directory`` (if present) is ingested
first, in filename order, so an older version committed there (e.g.
``fabric-receiving-inspection-v1.md``) becomes version 1 and is superseded
by the current file of the same slug in ``directory`` itself. Ingestion is
idempotent by ``(slug, sha256)``: a file whose content already has an
ACTIVE version under its slug is skipped.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Factory
from app.retrieval.embedder import Embedder
from app.retrieval.pipeline import _ingest, process_document_version
from app.retrieval.storage import DocumentStorage

_FRONT_MATTER_DELIM = "---"


class CorpusFrontMatterError(ValueError):
    pass


def _parse_front_matter(raw_text: str, *, path: Path) -> dict[str, Any]:
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != _FRONT_MATTER_DELIM:
        raise CorpusFrontMatterError(f"{path}: missing YAML front matter")
    try:
        closing = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise CorpusFrontMatterError(f"{path}: unterminated YAML front matter") from exc
    front_matter = yaml.safe_load("\n".join(lines[1:closing])) or {}
    if not isinstance(front_matter, dict):
        raise CorpusFrontMatterError(f"{path}: front matter must be a mapping")
    for field in ("slug", "title", "doc_type"):
        if not front_matter.get(field):
            raise CorpusFrontMatterError(f"{path}: front matter is missing {field!r}")
    return front_matter


async def _factory_id_for_scope(
    session: AsyncSession, *, organization_id: uuid.UUID, scope: str
) -> uuid.UUID | None:
    if scope == "org":
        return None
    factory_id = await session.scalar(
        select(Factory.id).where(Factory.organization_id == organization_id, Factory.code == scope)
    )
    if factory_id is None:
        raise CorpusFrontMatterError(f"unknown factory code {scope!r} in scope")
    return factory_id


def _corpus_files(directory: Path) -> list[Path]:
    versions_dir = directory / "versions"
    files: list[Path] = []
    if versions_dir.is_dir():
        files.extend(sorted(versions_dir.glob("*.md")))
    files.extend(
        sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in (".md", ".txt"))
    )
    return files


async def load_corpus_directory(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: uuid.UUID,
    directory: Path,
    embedder: Embedder,
    storage: DocumentStorage,
    created_by: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Ingest every ``*.md``/``*.txt`` file directly under ``directory`` (see module docstring).

    Returns the ids of the versions actually created (skips files whose
    content is already an ACTIVE version of the same slug).
    """
    created_version_ids: list[uuid.UUID] = []
    for path in _corpus_files(directory):
        raw = path.read_bytes()
        front_matter = _parse_front_matter(raw.decode("utf-8"), path=path)
        scope = str(front_matter.get("scope", "org"))
        acl_roles = [str(role) for role in (front_matter.get("acl") or [])]

        async with session_factory() as session, session.begin():
            factory_id = await _factory_id_for_scope(
                session, organization_id=organization_id, scope=scope
            )
            version = await _ingest(
                session,
                organization_id=organization_id,
                factory_id=factory_id,
                slug=str(front_matter["slug"]),
                title=str(front_matter["title"]),
                doc_type=str(front_matter["doc_type"]),
                acl_roles=acl_roles,
                filename=path.name,
                data=raw,
                created_by=created_by,
                storage=storage,
            )
            version_id = version.id if version is not None else None

        if version_id is not None:
            await process_document_version(
                session_factory, version_id, embedder=embedder, storage=storage
            )
            created_version_ids.append(version_id)

    return created_version_ids
