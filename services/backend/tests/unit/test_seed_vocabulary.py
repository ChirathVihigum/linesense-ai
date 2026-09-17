from __future__ import annotations

from app.seed import vocabulary


def test_order_refs_counts_and_uniqueness() -> None:
    assert len(vocabulary.KTN_ORDER_REFS) == 80
    assert vocabulary.KTN_ORDER_REFS[0] == "PO-KTN-0001"
    assert vocabulary.KTN_ORDER_REFS[-1] == "PO-KTN-0080"

    assert len(vocabulary.BYG_ORDER_REFS) == 20
    assert vocabulary.BYG_ORDER_REFS[0] == "PO-BYG-0001"
    assert vocabulary.BYG_ORDER_REFS[-1] == "PO-BYG-0020"

    assert vocabulary.DEMO_ORDER_REF == "PO-DEMO-001"

    all_refs = vocabulary.ALL_ORDER_REFS
    assert len(all_refs) == len(set(all_refs)) == 101
    assert set(all_refs) == {
        *vocabulary.KTN_ORDER_REFS,
        *vocabulary.BYG_ORDER_REFS,
        vocabulary.DEMO_ORDER_REF,
    }


def test_materials_counts_and_uniqueness() -> None:
    materials = vocabulary.MATERIALS
    assert len(materials) == 20
    codes = [code for code, _, _ in materials]
    names = [name for _, name, _ in materials]
    assert codes == [f"M{i:02d}" for i in range(1, 21)]
    assert len(set(codes)) == len(codes)
    assert len(set(names)) == len(names)
    for _, _, unit in materials:
        assert unit in {"m", "cone", "pcs"}


def test_operation_catalog_and_skill_codes() -> None:
    operations = vocabulary.OPERATION_CATALOG
    assert len(operations) == 13
    names = [name for name, _ in operations]
    assert len(set(names)) == len(names)

    skill_codes = {code for _, code in operations}
    assert skill_codes == set(vocabulary.SKILL_CODES)
    assert len(vocabulary.SKILL_CODES) == len(set(vocabulary.SKILL_CODES))
    assert ("collar attach", "SNLS") in operations
    assert ("final trim", "QC") in operations


def test_defect_catalog_counts_and_severities() -> None:
    defects = vocabulary.DEFECT_CATALOG
    assert len(defects) == 10
    codes = [code for code, _, _ in defects]
    names = [name for _, name, _ in defects]
    assert len(set(codes)) == len(codes)
    assert len(set(names)) == len(names)
    severities = {severity for _, _, severity in defects}
    assert severities <= {"MINOR", "MAJOR", "CRITICAL"}
    assert ("DEF-MC", "metal contamination", "CRITICAL") in defects


def test_lines() -> None:
    assert tuple((f"L{i}", f"Line {i}") for i in range(1, 7)) == vocabulary.KTN_LINES
    assert tuple((f"B{i}", f"Line B{i}") for i in range(1, 4)) == vocabulary.BYG_LINES
    all_codes = [code for code, _ in (*vocabulary.KTN_LINES, *vocabulary.BYG_LINES)]
    assert len(set(all_codes)) == len(all_codes) == 9


def test_style_codes() -> None:
    styles = vocabulary.STYLE_CODES
    assert len(styles) == 12
    assert styles[0] == "ST-01"
    assert styles[-1] == "ST-12"
    assert len(set(styles)) == len(styles)
