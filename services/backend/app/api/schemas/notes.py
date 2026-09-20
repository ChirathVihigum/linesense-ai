"""Request/response schemas for `app.api.notes` (task-18-brief.md requirement 3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

#: Shown on every note response so nobody mistakes an entity link for an
#: authorization: it is navigation only, never a basis for a write.
ENTITY_NOTICE = "Entity links are for navigation only; they do not authorize any action."


class NoteCreate(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


class EntityMentionOut(BaseModel):
    label: str
    text: str
    start: int
    end: int
    resolved_id: str | None


class NoteOut(BaseModel):
    id: uuid.UUID
    text: str
    classification: str
    classifier_version: str
    entities: list[EntityMentionOut]
    #: Well-formed order references (`PO-XXX-9999`) that did not resolve to
    #: a real order. Populated on create; always empty when a note is read
    #: back later, because `notes.entities` (backend-contracts.md section 2)
    #: only ever stores resolved entities, not this transient list.
    unresolved: list[EntityMentionOut]
    created_at: datetime
    notice: str = ENTITY_NOTICE
