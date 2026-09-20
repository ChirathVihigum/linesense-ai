"""Request/response schemas for `app.api.summaries` (task-18-brief.md requirement 4)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class SentenceOut(BaseModel):
    text: str
    evidence_ids: list[str]


class StatusSummaryOut(BaseModel):
    summary_source: str
    sentences: list[SentenceOut]
    report_run_id: uuid.UUID
    stale: bool
    label: str | None
