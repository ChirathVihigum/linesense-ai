"""Fixed demo vocabulary — exact strings referenced by the seed data
generator (Task 6), the synthetic SOP corpus, and the labelled NLP/IR
evaluation datasets (`data/synthetic/`, `data/eval/`, Task 16).

These values are a contract: the synthetic SOP text and the gold-label
entity spans in `data/eval/notes_*.jsonl` are authored against the exact
strings defined here (order refs, material/line/style codes and names,
operation names, defect codes). Do not rename or reorder anything here
without regenerating the affected datasets and re-running
`scripts/validate_datasets.py`.

Pure data only — no I/O, no database access.
"""

from __future__ import annotations

# --- Order references ---------------------------------------------------
# Katunayake (KTN) plant orders, Biyagama (BYG) plant orders, and one
# cross-factory demo order used in walkthroughs and screenshots.

KTN_ORDER_REFS: tuple[str, ...] = tuple(f"PO-KTN-{i:04d}" for i in range(1, 81))
BYG_ORDER_REFS: tuple[str, ...] = tuple(f"PO-BYG-{i:04d}" for i in range(1, 21))
DEMO_ORDER_REF: str = "PO-DEMO-001"

ALL_ORDER_REFS: tuple[str, ...] = (*KTN_ORDER_REFS, *BYG_ORDER_REFS, DEMO_ORDER_REF)

# --- Materials: (code, name, unit) --------------------------------------

MATERIALS: tuple[tuple[str, str, str], ...] = (
    ("M01", "Cotton Pique Fabric", "m"),
    ("M02", "Cotton Jersey Fabric", "m"),
    ("M03", "Polyester Mesh Fabric", "m"),
    ("M04", "Denim Twill Fabric", "m"),
    ("M05", "Rib Knit Collar Fabric", "m"),
    ("M06", "Fusible Interlining", "m"),
    ("M07", "Polyester Thread 40s", "cone"),
    ("M08", "Cotton Thread 50s", "cone"),
    ("M09", "Overlock Thread", "cone"),
    ("M10", "Button 18L", "pcs"),
    ("M11", "Metal Zipper 7in", "pcs"),
    ("M12", "Care Label", "pcs"),
    ("M13", "Brand Label", "pcs"),
    ("M14", "Hang Tag", "pcs"),
    ("M15", "Poly Bag", "pcs"),
    ("M16", "Carton Box", "pcs"),
    ("M17", "Elastic Tape 25mm", "pcs"),
    ("M18", "Twill Tape", "pcs"),
    ("M19", "Snap Fastener", "pcs"),
    ("M20", "Drawcord", "pcs"),
)

# --- Operation catalog: (name, skill_code), in style sequence order -----
# Style operations (`style_operations`) reference these names and are
# numbered `OP-01`, `OP-02`, ... in sequence order for a given style.

OPERATION_CATALOG: tuple[tuple[str, str], ...] = (
    ("shoulder join", "OL"),
    ("collar attach", "SNLS"),
    ("placket attach", "SNLS"),
    ("sleeve set", "OL"),
    ("side seam", "OL"),
    ("bottom hem", "FL"),
    ("sleeve hem", "FL"),
    ("buttonhole", "BH"),
    ("button attach", "BT"),
    ("label attach", "SNLS"),
    ("bartack", "BT"),
    ("pressing", "PRESS"),
    ("final trim", "QC"),
)

SKILL_CODES: tuple[str, ...] = tuple(sorted({code for _, code in OPERATION_CATALOG}))

# --- Defect catalog: (code, name, severity) -----------------------------

DEFECT_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("DEF-OS", "open seam", "MAJOR"),
    ("DEF-SS", "skipped stitch", "MAJOR"),
    ("DEF-BS", "broken stitch", "MAJOR"),
    ("DEF-ST", "stain", "MINOR"),
    ("DEF-SV", "shade variation", "MAJOR"),
    ("DEF-MO", "measurement out of tolerance", "MAJOR"),
    ("DEF-NH", "needle hole", "MAJOR"),
    ("DEF-PK", "puckering", "MINOR"),
    ("DEF-WL", "wrong label", "MAJOR"),
    ("DEF-MC", "metal contamination", "CRITICAL"),
)

# --- Lines: (code, name) -------------------------------------------------

KTN_LINES: tuple[tuple[str, str], ...] = tuple((f"L{i}", f"Line {i}") for i in range(1, 7))
BYG_LINES: tuple[tuple[str, str], ...] = tuple((f"B{i}", f"Line B{i}") for i in range(1, 4))

# --- Styles ---------------------------------------------------------------

STYLE_CODES: tuple[str, ...] = tuple(f"ST-{i:02d}" for i in range(1, 13))
