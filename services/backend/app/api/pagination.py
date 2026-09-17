"""Collection pagination (backend-contracts.md section 5)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


class Page[ItemT](BaseModel):
    items: list[ItemT]
    total: int
    limit: int
    offset: int
