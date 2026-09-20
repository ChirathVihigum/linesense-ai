"""Master-data-grounded entity extraction (task-18-brief.md requirement 1).

``EntityExtractor`` never invents or "generates" entities: it is a
``spacy.blank("en")`` pipeline with a single ``EntityRuler`` whose patterns
are compiled directly from a factory's own master data (plus one generic
regex pattern that flags well-formed-but-unknown order references so they
can be surfaced to the caller without ever being resolved or acted upon).
No spaCy model download, no statistical NER component.

Normalization/keys: every mapping in :class:`MasterData` is keyed by the
*lowercased* surface form (order external ref, line code/name, style code,
material code/name, operation name/plural, defect code/name). Overlap
between patterns (e.g. a material named ``Fabric`` and one named ``Cotton
Pique Fabric``) is resolved by spaCy's own ``EntityRuler``, which keeps the
longest non-overlapping span among its own matches.

Ambiguity: a normalized key that legitimately belongs to two different
records (documented as possible only for material *names*, which are not
unique in the schema — only ``(organization_id, code)`` is) is recorded as
:data:`AMBIGUOUS_ID` rather than either record's real id; the extractor
turns that sentinel into ``ambiguous=True, resolved_id=None``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import spacy
from spacy.lang.punctuation import TOKENIZER_INFIXES
from spacy.language import Language
from spacy.util import compile_infix_regex
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Line, Material, Order, Style
from app.seed.vocabulary import DEFECT_CATALOG, OPERATION_CATALOG

#: Sentinel used in place of a real id when two distinct records normalize
#: to the same surface form. The nil UUID can never collide with a real
#: ``uuid.uuid4()`` id (backend-contracts.md section 1).
AMBIGUOUS_ID = uuid.UUID(int=0)

_ORDER_REF_TOKEN_REGEX = r"^po-[a-z]{3}-\d{4}$"  # noqa: S105 - regex, not a secret


@dataclass(frozen=True)
class MasterData:
    """A factory's (plus its organization's) entity-resolvable vocabulary.

    ``orders``/``lines`` are factory-scoped; ``styles``/``materials`` are
    organization-scoped (schema has no ``factory_id`` on those tables);
    ``operations``/``defects`` come from the fixed seed catalogue
    (``app.seed.vocabulary``), identical for every organization/factory.
    """

    orders: Mapping[str, uuid.UUID]
    lines: Mapping[str, uuid.UUID]
    styles: Mapping[str, uuid.UUID]
    materials: Mapping[str, tuple[uuid.UUID, str]]
    operations: Mapping[str, str]
    defects: Mapping[str, str]


@dataclass(frozen=True)
class EntityMention:
    label: str
    text: str
    start: int
    end: int
    normalized: str
    resolved_id: str | None
    ambiguous: bool


def _put_id(mapping: dict[str, uuid.UUID], key: str, value: uuid.UUID) -> None:
    existing = mapping.get(key)
    if existing is None:
        mapping[key] = value
    elif existing != value:
        mapping[key] = AMBIGUOUS_ID


def _put_material(
    mapping: dict[str, tuple[uuid.UUID, str]], key: str, value: uuid.UUID, code: str
) -> None:
    existing = mapping.get(key)
    if existing is None:
        mapping[key] = (value, code)
    elif existing[0] not in (value, AMBIGUOUS_ID):
        mapping[key] = (AMBIGUOUS_ID, "")


def _operation_patterns() -> dict[str, str]:
    patterns: dict[str, str] = {}
    for name, _skill_code in OPERATION_CATALOG:
        patterns[name.lower()] = name
        patterns[f"{name}s".lower()] = name
    return patterns


def _defect_patterns() -> dict[str, str]:
    patterns: dict[str, str] = {}
    for code, name, _severity in DEFECT_CATALOG:
        patterns[code.lower()] = code
        patterns[name.lower()] = code
    return patterns


async def load_master_data(
    session: AsyncSession, organization_id: uuid.UUID, factory_id: uuid.UUID
) -> MasterData:
    """Build the entity-resolution vocabulary for one organization/factory.

    Only records in ``organization_id`` (and, for orders/lines, in
    ``factory_id``) are included, so a note can never resolve an entity
    belonging to another tenant or another factory (task-18-brief.md
    requirement 3: "Entity resolution only uses master data in the
    caller's org/factory").
    """
    orders: dict[str, uuid.UUID] = {}
    for external_ref, order_id in await session.execute(
        select(Order.external_ref, Order.id).where(
            Order.organization_id == organization_id, Order.factory_id == factory_id
        )
    ):
        _put_id(orders, external_ref.lower(), order_id)

    lines: dict[str, uuid.UUID] = {}
    for code, name, line_id in await session.execute(
        select(Line.code, Line.name, Line.id).where(
            Line.organization_id == organization_id, Line.factory_id == factory_id
        )
    ):
        _put_id(lines, code.lower(), line_id)
        _put_id(lines, name.lower(), line_id)

    styles: dict[str, uuid.UUID] = {}
    for code, style_id in await session.execute(
        select(Style.code, Style.id).where(Style.organization_id == organization_id)
    ):
        _put_id(styles, code.lower(), style_id)

    materials: dict[str, tuple[uuid.UUID, str]] = {}
    for code, name, material_id in await session.execute(
        select(Material.code, Material.name, Material.id).where(
            Material.organization_id == organization_id
        )
    ):
        _put_material(materials, code.lower(), material_id, code)
        _put_material(materials, name.lower(), material_id, code)

    return MasterData(
        orders=orders,
        lines=lines,
        styles=styles,
        materials=materials,
        operations=_operation_patterns(),
        defects=_defect_patterns(),
    )


def _loosen_hyphen_tokenization(nlp: Language) -> None:
    """Stop the tokenizer splitting ``DEF-OS``/``PO-KTN-0020`` apart.

    spaCy's default English infixes only split a hyphen between two
    *alphabetic* runs (so ``ST-01``/``KTN-0020`` already survive as one
    token, but ``DEF-OS`` and the ``PO``/``KTN`` halves of an order ref do
    not). Every code in the seed vocabulary uses a hyphen as an internal
    delimiter and none of this pipeline's patterns rely on hyphen-splitting
    for anything else, so the alpha-hyphen-alpha infix rule is dropped.
    """
    kept = [pattern for pattern in TOKENIZER_INFIXES if not pattern.startswith("(?<=[A-Za-z")]
    nlp.tokenizer.infix_finditer = compile_infix_regex(kept).finditer  # type: ignore[union-attr]


def _build_pipeline(master: MasterData) -> Language:
    nlp = spacy.blank("en")
    _loosen_hyphen_tokenization(nlp)
    ruler: Any = nlp.add_pipe("entity_ruler", config={"phrase_matcher_attr": "LOWER"})
    patterns: list[dict[str, Any]] = [{"label": "ORDER", "pattern": ref} for ref in master.orders]
    patterns.append({"label": "ORDER", "pattern": [{"LOWER": {"REGEX": _ORDER_REF_TOKEN_REGEX}}]})
    patterns.extend({"label": "LINE", "pattern": key} for key in master.lines)
    patterns.extend({"label": "STYLE", "pattern": key} for key in master.styles)
    patterns.extend({"label": "MATERIAL", "pattern": key} for key in master.materials)
    patterns.extend({"label": "OPERATION", "pattern": key} for key in master.operations)
    patterns.extend({"label": "DEFECT", "pattern": key} for key in master.defects)
    ruler.add_patterns(patterns)
    return nlp


class EntityExtractor:
    """Resolves entity mentions in note text against one factory's master data."""

    def __init__(self, master: MasterData) -> None:
        self._master = master
        self._nlp = _build_pipeline(master)

    def extract(self, text: str) -> list[EntityMention]:
        doc = self._nlp(text)
        mentions: list[EntityMention] = []
        for ent in doc.ents:
            normalized = ent.text.lower()
            resolved_id, ambiguous = self._resolve(ent.label_, normalized)
            mentions.append(
                EntityMention(
                    label=ent.label_,
                    text=ent.text,
                    start=ent.start_char,
                    end=ent.end_char,
                    normalized=normalized,
                    resolved_id=resolved_id,
                    ambiguous=ambiguous,
                )
            )
        return mentions

    def _resolve(self, label: str, normalized: str) -> tuple[str | None, bool]:
        if label == "ORDER":
            return self._resolve_id(self._master.orders.get(normalized))
        if label == "LINE":
            return self._resolve_id(self._master.lines.get(normalized))
        if label == "STYLE":
            return self._resolve_id(self._master.styles.get(normalized))
        if label == "MATERIAL":
            entry = self._master.materials.get(normalized)
            if entry is None:
                return None, False
            return self._resolve_id(entry[0])
        if label == "OPERATION":
            hit = self._master.operations.get(normalized)
            return (hit, False) if hit is not None else (None, False)
        if label == "DEFECT":
            hit = self._master.defects.get(normalized)
            return (hit, False) if hit is not None else (None, False)
        return None, False

    @staticmethod
    def _resolve_id(value: uuid.UUID | None) -> tuple[str | None, bool]:
        if value is None:
            return None, False
        if value == AMBIGUOUS_ID:
            return None, True
        return str(value), False
