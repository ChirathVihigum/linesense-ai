"""Notes routes: create (classify + extract) and list (task-18-brief.md
requirement 3).

Notes carry no business authority: entity links are navigation-only (never
resolved, created, or acted upon by anything downstream), and
classification is informational. The one permission the contract defines
for notes, `note:create`, gates both routes here — there is no separate
`note:read` in backend-contracts.md section 4, so a principal who may not
add notes to a factory may not browse them either.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_principal, require_idempotency_key
from app.api.errors import AppError
from app.api.orders import audit_denial_from_error, idempotent_finish, idempotent_start
from app.api.orders import request_trace_id as _request_trace_id
from app.api.pagination import Page, PageParams, page_params
from app.api.schemas.notes import EntityMentionOut, NoteCreate, NoteOut
from app.audit.service import record_audit
from app.auth.policy import Principal
from app.auth.scope import load_scoped
from app.db.models import Factory, Note
from app.db.session import get_db_session
from app.domain.vocab import ActorType, AuditOutcome
from app.nlp.classifier import get_default_classifier, get_default_classifier_version
from app.nlp.entities import EntityExtractor, EntityMention, load_master_data

router = APIRouter(prefix="/api/v1", tags=["notes"])

NOTE_PERMISSION = "note:create"


def _mention_out(mention: EntityMention) -> EntityMentionOut:
    return EntityMentionOut(
        label=mention.label,
        text=mention.text,
        start=mention.start,
        end=mention.end,
        resolved_id=mention.resolved_id,
    )


def _mention_dict(mention: EntityMention) -> dict[str, Any]:
    return {
        "label": mention.label,
        "text": mention.text,
        "start": mention.start,
        "end": mention.end,
        "resolved_id": mention.resolved_id,
    }


def _stored_entities_out(rows: list[dict[str, Any]]) -> list[EntityMentionOut]:
    return [
        EntityMentionOut(
            label=str(row.get("label")),
            text=str(row.get("text")),
            start=int(row.get("start", 0)),
            end=int(row.get("end", 0)),
            resolved_id=row.get("resolved_id"),
        )
        for row in rows
    ]


def _note_out(note: Note, *, unresolved: list[EntityMentionOut] | None = None) -> NoteOut:
    entities_raw: list[dict[str, Any]] = note.entities if isinstance(note.entities, list) else []
    return NoteOut(
        id=note.id,
        text=note.text,
        classification=note.classification,
        classifier_version=get_default_classifier_version(),
        entities=_stored_entities_out(entities_raw),
        unresolved=unresolved or [],
        created_at=note.created_at,
    )


@router.post("/factories/{factory_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(
    factory_id: uuid.UUID,
    body: NoteCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    request_payload = {"factory_id": str(factory_id), "text": body.text}
    early = await idempotent_start(
        session,
        principal,
        operation="note:create",
        key=idempotency_key,
        request_payload=request_payload,
    )
    if early is not None:
        return early

    try:
        factory = await load_scoped(session, Factory, factory_id, principal, NOTE_PERMISSION)
    except AppError as exc:
        await audit_denial_from_error(
            request,
            principal,
            exc,
            factory_id=factory_id,
            action="note.create",
            target_type="factory",
            target_id=str(factory_id),
        )
        raise

    master = await load_master_data(session, principal.organization_id, factory.id)
    mentions = EntityExtractor(master).extract(body.text)
    resolved = [m for m in mentions if m.resolved_id is not None and not m.ambiguous]
    unresolved = [
        m for m in mentions if m.label == "ORDER" and m.resolved_id is None and not m.ambiguous
    ]

    classification, _margin = get_default_classifier().predict_with_margin([body.text])[0]

    note = Note(
        organization_id=principal.organization_id,
        factory_id=factory.id,
        author_id=principal.user_id,
        text=body.text,
        classification=classification,
        entities=[_mention_dict(m) for m in resolved],
    )
    session.add(note)
    await session.flush()
    await session.refresh(note)

    await record_audit(
        session,
        organization_id=principal.organization_id,
        factory_id=factory.id,
        actor_type=ActorType.USER.value,
        actor_id=str(principal.user_id),
        action="note.create",
        target_type="note",
        target_id=str(note.id),
        outcome=AuditOutcome.SUCCESS.value,
        trace_id=_request_trace_id(request),
    )

    out = _note_out(note, unresolved=[_mention_out(m) for m in unresolved])
    return await idempotent_finish(
        session,
        principal,
        operation="note:create",
        key=idempotency_key,
        status_code=201,
        body=out,
    )


@router.get("/factories/{factory_id}/notes", response_model=Page[NoteOut])
async def list_notes(
    factory_id: uuid.UUID,
    classification: str | None = Query(default=None),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Page[NoteOut]:
    factory = await load_scoped(session, Factory, factory_id, principal, NOTE_PERMISSION)
    stmt = select(Note).where(
        Note.organization_id == principal.organization_id, Note.factory_id == factory.id
    )
    if classification is not None:
        stmt = stmt.where(Note.classification == classification)
    total = await session.scalar(select(sa.func.count()).select_from(stmt.subquery()))
    rows = (
        await session.scalars(
            stmt.order_by(Note.created_at.desc(), Note.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    ).all()
    items = [_note_out(row) for row in rows]
    return Page[NoteOut](items=items, total=int(total or 0), limit=page.limit, offset=page.offset)
