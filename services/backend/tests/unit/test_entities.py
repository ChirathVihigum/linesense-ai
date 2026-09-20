"""Unit tests for `app.nlp.entities` (task-18-brief.md requirement 1)."""

from __future__ import annotations

import uuid

import pytest

from app.nlp.entities import AMBIGUOUS_ID, EntityExtractor, MasterData

ORDER_ID = uuid.UUID(int=1)
LINE_ID = uuid.UUID(int=2)
STYLE_ID = uuid.UUID(int=3)
MATERIAL_ID = uuid.UUID(int=4)
OTHER_MATERIAL_ID = uuid.UUID(int=5)


@pytest.fixture
def master() -> MasterData:
    return MasterData(
        orders={"po-ktn-0012": ORDER_ID},
        lines={
            "l3": LINE_ID,
            "line 3": LINE_ID,
        },
        styles={"st-03": STYLE_ID},
        materials={
            "m01": (MATERIAL_ID, "M01"),
            "cotton pique fabric": (MATERIAL_ID, "M01"),
            "fabric": (OTHER_MATERIAL_ID, "M05"),
            "shared name": (AMBIGUOUS_ID, ""),
        },
        operations={"collar attach": "collar attach", "collar attachs": "collar attach"},
        defects={"def-os": "DEF-OS", "open seam": "DEF-OS"},
    )


@pytest.fixture
def extractor(master: MasterData) -> EntityExtractor:
    return EntityExtractor(master)


def test_known_order_ref_resolves_case_insensitively(extractor: EntityExtractor) -> None:
    mentions = extractor.extract("Order po-KTN-0012 needs review today.")
    orders = [m for m in mentions if m.label == "ORDER"]
    assert len(orders) == 1
    assert orders[0].resolved_id == str(ORDER_ID)
    assert orders[0].ambiguous is False
    assert orders[0].text == "po-KTN-0012"


def test_unknown_but_well_formed_order_ref_is_returned_unresolved(
    extractor: EntityExtractor,
) -> None:
    mentions = extractor.extract("An old note mentions PO-KTN-9999 from last season.")
    orders = [m for m in mentions if m.label == "ORDER"]
    assert len(orders) == 1
    assert orders[0].resolved_id is None
    assert orders[0].ambiguous is False


def test_malformed_order_like_text_is_not_matched(extractor: EntityExtractor) -> None:
    mentions = extractor.extract("Reference PO-99 is not order-shaped at all.")
    assert not [m for m in mentions if m.label == "ORDER"]


@pytest.mark.parametrize("surface", ["L3", "Line 3", "line 3"])
def test_line_surface_forms_resolve_to_the_same_line(
    extractor: EntityExtractor, surface: str
) -> None:
    mentions = extractor.extract(f"{surface} is running ahead of schedule.")
    lines = [m for m in mentions if m.label == "LINE"]
    assert len(lines) == 1
    assert lines[0].resolved_id == str(LINE_ID)


def test_byg_line_codes_are_unresolved_for_ktn_master_data(master: MasterData) -> None:
    # `master` only knows about a KTN-style line (L3); a BYG line code/name
    # never appears as a pattern, so it is not even recognised as an entity.
    extractor = EntityExtractor(master)
    mentions = extractor.extract("Line B1 picked up the overflow units.")
    assert not [m for m in mentions if m.label == "LINE"]


def test_style_code_resolves(extractor: EntityExtractor) -> None:
    mentions = extractor.extract("Committing ST-03 to the next shift.")
    styles = [m for m in mentions if m.label == "STYLE"]
    assert len(styles) == 1
    assert styles[0].resolved_id == str(STYLE_ID)


def test_material_code_and_name_resolve_case_insensitively(extractor: EntityExtractor) -> None:
    by_code = extractor.extract("M01 stock is running low.")
    by_name = extractor.extract("cotton pique fabric stock is running low.")
    assert [m.resolved_id for m in by_code if m.label == "MATERIAL"] == [str(MATERIAL_ID)]
    assert [m.resolved_id for m in by_name if m.label == "MATERIAL"] == [str(MATERIAL_ID)]


def test_longest_material_match_wins_over_a_shorter_overlapping_pattern(
    extractor: EntityExtractor,
) -> None:
    mentions = extractor.extract("The order needs Cotton Pique Fabric before Friday.")
    materials = [m for m in mentions if m.label == "MATERIAL"]
    assert len(materials) == 1
    assert materials[0].text == "Cotton Pique Fabric"
    assert materials[0].resolved_id == str(MATERIAL_ID)


def test_ambiguous_surface_form_resolves_to_neither_record(extractor: EntityExtractor) -> None:
    mentions = extractor.extract("shared name appears on two material rows.")
    materials = [m for m in mentions if m.label == "MATERIAL"]
    assert len(materials) == 1
    assert materials[0].resolved_id is None
    assert materials[0].ambiguous is True


def test_operation_catalog_name_and_plural(extractor: EntityExtractor) -> None:
    singular = extractor.extract("collar attach ran smoothly.")
    plural = extractor.extract("collar attachs ran smoothly.")
    assert [m.resolved_id for m in singular if m.label == "OPERATION"] == ["collar attach"]
    assert [m.resolved_id for m in plural if m.label == "OPERATION"] == ["collar attach"]


def test_defect_code_and_name_resolve(extractor: EntityExtractor) -> None:
    by_code = extractor.extract("DEF-OS found on the sleeve.")
    by_name = extractor.extract("open seam found on the sleeve.")
    assert [m.resolved_id for m in by_code if m.label == "DEFECT"] == ["DEF-OS"]
    assert [m.resolved_id for m in by_name if m.label == "DEFECT"] == ["DEF-OS"]
