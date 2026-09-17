#!/usr/bin/env python3
"""Generator for the labelled shift-note NLP datasets
(`data/eval/notes_train.jsonl`, `data/eval/notes_test.jsonl`) used by
Task 16's NLP/IR evaluation.

Notes are assembled from hand-written sentence templates plus vocabulary
mentions drawn from `app.seed.vocabulary`; entity character offsets are
computed programmatically while the text is built (never guessed by hand),
so `text[start:end] == entity_text` holds by construction.

Train and test notes are generated from **disjoint** template sets and
distinct phrasing conventions, and a fixed random seed
(``RANDOM_SEED``) makes generation reproducible. As a final safety check,
every generated test note's token-set Jaccard similarity is checked
against every generated train note and must stay below the validator's
0.8 threshold (`scripts/validate_datasets.py::JACCARD_MAX`); a note that
fails is regenerated.

Run with: `python3 scripts/build_notes_dataset.py` (no backend dependency
group besides the `app` package import; run from repo root or anywhere,
paths are resolved from this file's location).
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "services" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_datasets import JACCARD_MAX, jaccard_similarity, token_set  # noqa: E402

from app.seed import vocabulary as vocab  # noqa: E402

RANDOM_SEED = 20260917

EVAL_DIR = REPO_ROOT / "data" / "eval"
TRAIN_PATH = EVAL_DIR / "notes_train.jsonl"
TEST_PATH = EVAL_DIR / "notes_test.jsonl"

NOTES_PER_LABEL_TRAIN = 30  # 5 labels * 30 = 150
NOTES_PER_LABEL_TEST = 22  # 5 labels * 22 = 110

LABELS = ["planning", "materials", "ie", "quality", "unknown"]

# ---------------------------------------------------------------------
# Vocabulary pools
# ---------------------------------------------------------------------

ORDERS_KTN = list(vocab.KTN_ORDER_REFS)
ORDERS_BYG = list(vocab.BYG_ORDER_REFS)
ALL_ORDERS = ORDERS_KTN + ORDERS_BYG + [vocab.DEMO_ORDER_REF]
KTN_LINES = list(vocab.KTN_LINES)
BYG_LINES = list(vocab.BYG_LINES)
ALL_LINES = KTN_LINES + BYG_LINES
STYLES = list(vocab.STYLE_CODES)
MATERIALS = list(vocab.MATERIALS)
OPERATIONS = list(vocab.OPERATION_CATALOG)
DEFECTS = list(vocab.DEFECT_CATALOG)

FAKE_ORDER_REF = "PO-KTN-9999"  # intentionally not in vocab.KTN_ORDER_REFS


class Entity:
    __slots__ = ("label", "text")

    def __init__(self, label: str, text: str) -> None:
        self.label = label
        self.text = text


Segment = str | Entity


def ent(label: str, text: str) -> Entity:
    return Entity(label, text)


def render(parts: list[Segment]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    entities: list[dict[str, Any]] = []
    cursor = 0
    for part in parts:
        if isinstance(part, Entity):
            text_parts.append(part.text)
            entities.append(
                {
                    "label": part.label,
                    "text": part.text,
                    "start": cursor,
                    "end": cursor + len(part.text),
                }
            )
            cursor += len(part.text)
        else:
            text_parts.append(part)
            cursor += len(part)
    return "".join(text_parts), entities


# ---------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------


def order_mention(rng: random.Random, plant: str | None = None) -> str:
    if plant == "KTN":
        return rng.choice(ORDERS_KTN)
    if plant == "BYG":
        return rng.choice(ORDERS_BYG)
    return rng.choice(ALL_ORDERS)


def line_mention(rng: random.Random, plant: str | None = None) -> tuple[str, str]:
    pool = KTN_LINES if plant == "KTN" else BYG_LINES if plant == "BYG" else ALL_LINES
    code, name = rng.choice(pool)
    form = rng.choice(["code", "name", "name_lower"])
    if form == "code":
        return code, code
    if form == "name":
        return code, name
    return code, name.lower()


def style_mention(rng: random.Random) -> str:
    return rng.choice(STYLES)


def material_mention(rng: random.Random) -> tuple[str, str]:
    code, name, _unit = rng.choice(MATERIALS)
    form = rng.choice(["code", "name", "name_lower"])
    if form == "code":
        return code, code
    if form == "name":
        return code, name
    return code, name.lower()


def operation_mention(rng: random.Random) -> tuple[str, str]:
    name, _skill = rng.choice(OPERATIONS)
    return name, name


def defect_mention(rng: random.Random) -> tuple[str, str]:
    code, name, _severity = rng.choice(DEFECTS)
    form = rng.choice(["code", "name"])
    return code, (code if form == "code" else name)


# ---------------------------------------------------------------------
# Templates: TRAIN_TEMPLATES[label] and TEST_TEMPLATES[label] are
# disjoint lists of callables(rng) -> list[Segment]. Train and test use
# different sentence openers/closers and different random-choice pools
# so the two splits read as genuinely different writing, not paraphrase.
# ---------------------------------------------------------------------

TemplateFn = Callable[[random.Random], list[Segment]]

TRAIN_TEMPLATES: dict[str, list[TemplateFn]] = {
    "planning": [],
    "materials": [],
    "ie": [],
    "quality": [],
    "unknown": [],
}
TEST_TEMPLATES: dict[str, list[TemplateFn]] = {
    "planning": [],
    "materials": [],
    "ie": [],
    "quality": [],
    "unknown": [],
}


def train_template(label: str) -> Callable[[TemplateFn], TemplateFn]:
    def decorator(fn: TemplateFn) -> TemplateFn:
        TRAIN_TEMPLATES[label].append(fn)
        return fn

    return decorator


def test_template(label: str) -> Callable[[TemplateFn], TemplateFn]:
    def decorator(fn: TemplateFn) -> TemplateFn:
        TEST_TEMPLATES[label].append(fn)
        return fn

    return decorator


# --- planning -------------------------------------------------------


@train_template("planning")
def _p1(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    _, line = line_mention(rng)
    days = rng.choice(["one day", "two days", "three days"])
    return [
        ent("ORDER", order),
        f" is running {days} behind on ",
        ent("LINE", line),
        "; flagging for replan.",
    ]


@train_template("planning")
def _p2(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    style = style_mention(rng)
    return [
        ent("LINE", line),
        " finished changeover early and is ready to start ",
        ent("STYLE", style),
        " ahead of the loaded shift.",
    ]


@train_template("planning")
def _p3(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    _, line = line_mention(rng)
    return [
        "Supervisor moved ",
        ent("ORDER", order),
        " onto ",
        ent("LINE", line),
        " after the earlier slot filled up sooner than expected.",
    ]


@train_template("planning")
def _p4(rng: random.Random) -> list[Segment]:
    order1 = order_mention(rng)
    order2 = order_mention(rng)
    return [
        "Queue review: ",
        ent("ORDER", order1),
        " has priority over ",
        ent("ORDER", order2),
        " under earliest-due-date sequencing this shift.",
    ]


@train_template("planning")
def _p5(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    style = style_mention(rng)
    return [
        ent("LINE", line),
        " is loaded with ",
        ent("STYLE", style),
        " for the rest of the shift; no changeover planned before tomorrow.",
    ]


@train_template("planning")
def _p6(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        ent("ORDER", order),
        " capacity check done; remaining units fit in today's slot with margin to spare.",
    ]


@train_template("planning")
def _p7(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    return [
        ent("LINE", line),
        " has spare standard minutes this shift; planner asked to check if another "
        "order can be pulled forward.",
    ]


# --- planning (test, disjoint phrasing) ------------------------------


@test_template("planning")
def _pt1(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    _, line = line_mention(rng)
    return [
        "Planning note: reassigned ",
        ent("ORDER", order),
        " to ",
        ent("LINE", line),
        " for the remainder of today's run.",
    ]


@test_template("planning")
def _pt2(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    style = style_mention(rng)
    minutes = rng.choice(["twenty", "thirty", "forty-five"])
    return [
        ent("LINE", line),
        " changeover to ",
        ent("STYLE", style),
        " took about ",
        minutes,
        " minutes longer than planned.",
    ]


@test_template("planning")
def _pt3(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        "Due-date watch: ",
        ent("ORDER", order),
        " will miss its date unless an extra shift slot opens up before Friday.",
    ]


@test_template("planning")
def _pt4(rng: random.Random) -> list[Segment]:
    order1 = order_mention(rng)
    order2 = order_mention(rng)
    return [
        "Sequencing swap approved: ",
        ent("ORDER", order1),
        " now runs ahead of ",
        ent("ORDER", order2),
        " per the supervisor's note.",
    ]


@test_template("planning")
def _pt5(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    return [
        "Checked ",
        ent("LINE", line),
        "'s shift board; tomorrow's load is confirmed and no gaps remain.",
    ]


@test_template("planning")
def _pt6(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    _, line = line_mention(rng)
    return [
        "Loaded ",
        ent("ORDER", order),
        " onto ",
        ent("LINE", line),
        " a shift earlier than originally scheduled to cover a gap on another line.",
    ]


@test_template("planning")
def _pt7(rng: random.Random) -> list[Segment]:
    style = style_mention(rng)
    return [
        "Style ",
        ent("STYLE", style),
        " capacity plan reviewed with IE; no changes needed for next week's loading.",
    ]


# --- materials --------------------------------------------------------


@train_template("materials")
def _m1(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    order = order_mention(rng)
    return [
        "Short delivery of ",
        ent("MATERIAL", mat),
        " received; ",
        ent("ORDER", order),
        " may run short by end of shift.",
    ]


@train_template("materials")
def _m2(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        ent("MATERIAL", mat),
        " lot moved to quarantine pending inspection; storekeeper notified.",
    ]


@train_template("materials")
def _m3(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    order = order_mention(rng)
    return [
        "Reserved ",
        ent("MATERIAL", mat),
        " for ",
        ent("ORDER", order),
        " after checking the available balance this morning.",
    ]


@train_template("materials")
def _m4(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        "Supermarket bin for ",
        ent("MATERIAL", mat),
        " hit minimum quantity; pull card raised to the store.",
    ]


@train_template("materials")
def _m5(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    _, line = line_mention(rng)
    return [
        ent("MATERIAL", mat),
        " issued to ",
        ent("LINE", line),
        " against this morning's cutting ticket.",
    ]


@train_template("materials")
def _m6(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        "Reorder point review flagged ",
        ent("MATERIAL", mat),
        "; requisition raised to cover the next three weeks.",
    ]


@train_template("materials")
def _m7(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [ent("MATERIAL", mat), " lot accepted after inspection; balance updated for planning."]


# --- materials (test) ---------------------------------------------------


@test_template("materials")
def _mt1(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    order = order_mention(rng)
    return [
        "Materials update: ",
        ent("ORDER", order),
        " is on hold pending an incoming delivery of ",
        ent("MATERIAL", mat),
        ".",
    ]


@test_template("materials")
def _mt2(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        "Storekeeper rejected a lot of ",
        ent("MATERIAL", mat),
        " after finding it outside the acceptance threshold.",
    ]


@test_template("materials")
def _mt3(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        "Count discrepancy noted on ",
        ent("MATERIAL", mat),
        " during the weekly supermarket walk-through; recount scheduled.",
    ]


@test_template("materials")
def _mt4(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    _, line = line_mention(rng)
    return [
        ent("LINE", line),
        " requested an early top-up of ",
        ent("MATERIAL", mat),
        " ahead of tomorrow's higher-volume style.",
    ]


@test_template("materials")
def _mt5(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    order = order_mention(rng)
    return [
        "Released the ",
        ent("MATERIAL", mat),
        " reservation held for ",
        ent("ORDER", order),
        " after the order's quantity was revised down.",
    ]


@test_template("materials")
def _mt6(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        "Consumption of ",
        ent("MATERIAL", mat),
        " this week is tracking above the usual rate; worth a reorder-point recheck.",
    ]


@test_template("materials")
def _mt7(rng: random.Random) -> list[Segment]:
    code, mat = material_mention(rng)
    return [
        "Excess ",
        ent("MATERIAL", mat),
        " returned from the cutting table and credited back to store stock.",
    ]


# --- ie -----------------------------------------------------------------


@train_template("ie")
def _i1(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    _, line = line_mention(rng)
    return [
        ent("OPERATION", opname),
        " on ",
        ent("LINE", line),
        " is running slower than expected; worth a time study.",
    ]


@train_template("ie")
def _i2(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    pct = rng.choice(["68%", "72%", "80%"])
    return [
        ent("LINE", line),
        "'s balance index came in at ",
        pct,
        " this shift, below the usual range.",
    ]


@train_template("ie")
def _i3(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    style = style_mention(rng)
    return [
        ent("OPERATION", opname),
        " flagged as the bottleneck for ",
        ent("STYLE", style),
        " again today.",
    ]


@train_template("ie")
def _i4(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    return [
        "Two cycle-time readings for ",
        ent("OPERATION", opname),
        " were flagged as outliers and are pending IE review.",
    ]


@train_template("ie")
def _i5(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    style = style_mention(rng)
    return [
        "First-off check on ",
        ent("LINE", line),
        " for ",
        ent("STYLE", style),
        " passed; running at full speed now.",
    ]


@train_template("ie")
def _i6(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    return [
        "Reassigned a second operator to ",
        ent("OPERATION", opname),
        " to relieve today's bottleneck.",
    ]


@train_template("ie")
def _i7(rng: random.Random) -> list[Segment]:
    style = style_mention(rng)
    return [
        "Time study for ",
        ent("STYLE", style),
        " completed; ten cycles recorded per operation.",
    ]


# --- ie (test) ------------------------------------------------------------


@test_template("ie")
def _it1(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    _, line = line_mention(rng)
    return [
        "IE observed ",
        ent("OPERATION", opname),
        " on ",
        ent("LINE", line),
        " and confirmed it as today's constraint operation.",
    ]


@test_template("ie")
def _it2(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    return [
        "Bottleneck escalation opened for ",
        ent("LINE", line),
        " after the balance index stayed low for a second consecutive shift.",
    ]


@test_template("ie")
def _it3(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    return [
        "Method change trialled on ",
        ent("OPERATION", opname),
        "; cycle time improved slightly but needs a full re-study to confirm.",
    ]


@test_template("ie")
def _it4(rng: random.Random) -> list[Segment]:
    style = style_mention(rng)
    op, opname = operation_mention(rng)
    return [
        "SAM for ",
        ent("OPERATION", opname),
        " under ",
        ent("STYLE", style),
        " is being reviewed after a machine change last week.",
    ]


@test_template("ie")
def _it5(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    return [
        "Skill gap noted on ",
        ent("LINE", line),
        "; loading a style requiring that skill will need cross-training first.",
    ]


@test_template("ie")
def _it6(rng: random.Random) -> list[Segment]:
    op, opname = operation_mention(rng)
    return [
        "Outlier reading on ",
        ent("OPERATION", opname),
        " traced to a bundle delay, not a genuine slow cycle; excluded from the study.",
    ]


@test_template("ie")
def _it7(rng: random.Random) -> list[Segment]:
    style = style_mention(rng)
    return [
        ent("STYLE", style),
        "'s theoretical output was recalculated after yesterday's time study update.",
    ]


# --- quality --------------------------------------------------------------


@train_template("quality")
def _q1(rng: random.Random) -> list[Segment]:
    code, dname = defect_mention(rng)
    op, opname = operation_mention(rng)
    _, line = line_mention(rng)
    return [
        "Repeated ",
        ent("DEFECT", dname),
        " found at ",
        ent("OPERATION", opname),
        " on ",
        ent("LINE", line),
        "; inline inspector notified.",
    ]


@train_template("quality")
def _q2(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return ["FINAL inspection for ", ent("ORDER", order), " passed with two minor defects noted."]


@train_template("quality")
def _q3(rng: random.Random) -> list[Segment]:
    code, dname = defect_mention(rng)
    order = order_mention(rng)
    return [
        ent("ORDER", order),
        " placed on quality hold after a ",
        ent("DEFECT", dname),
        " finding during inline inspection.",
    ]


@train_template("quality")
def _q4(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        "Quality hold on ",
        ent("ORDER", order),
        " released after corrective action confirmed and a fresh sample passed.",
    ]


@train_template("quality")
def _q5(rng: random.Random) -> list[Segment]:
    code, dname = defect_mention(rng)
    return [
        ent("DEFECT", dname),
        " count is trending up this week; raised with the quality manager for review.",
    ]


@train_template("quality")
def _q6(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        ent("ORDER", order),
        " sample size was short of eighty units; result recorded as insufficient "
        "sample, re-inspection scheduled.",
    ]


@train_template("quality")
def _q7(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    return ["Metal detector on ", ent("LINE", line), " calibrated at shift start; no issues found."]


# --- quality (test) ---------------------------------------------------------


@test_template("quality")
def _qt1(rng: random.Random) -> list[Segment]:
    code, dname = defect_mention(rng)
    order = order_mention(rng)
    return [
        "Critical finding: ",
        ent("DEFECT", dname),
        " detected on a unit from ",
        ent("ORDER", order),
        "; bundle isolated pending investigation.",
    ]


@test_template("quality")
def _qt2(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        ent("ORDER", order),
        " cleared FINAL inspection this afternoon and is now eligible for packing.",
    ]


@test_template("quality")
def _qt3(rng: random.Random) -> list[Segment]:
    code, dname = defect_mention(rng)
    op, opname = operation_mention(rng)
    return [
        "Root cause for the recurring ",
        ent("DEFECT", dname),
        " traced back to ",
        ent("OPERATION", opname),
        "; operator re-briefed on the method.",
    ]


@test_template("quality")
def _qt4(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        "Second quality hold added to ",
        ent("ORDER", order),
        " for a separate labelling issue found this morning.",
    ]


@test_template("quality")
def _qt5(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    return [
        "Needle breakage on ",
        ent("LINE", line),
        " fully accounted for; no missing fragments, machine cleared to resume.",
    ]


@test_template("quality")
def _qt6(rng: random.Random) -> list[Segment]:
    code, dname = defect_mention(rng)
    return [
        "Defect catalogue query: confirming ",
        ent("DEFECT", dname),
        " is the correct code for this morning's finding before logging it.",
    ]


@test_template("quality")
def _qt7(rng: random.Random) -> list[Segment]:
    order = order_mention(rng)
    return [
        "Packing held for ",
        ent("ORDER", order),
        " pending release of an earlier quality hold.",
    ]


# --- unknown ----------------------------------------------------------------


@train_template("unknown")
def _u1(rng: random.Random) -> list[Segment]:
    shift = rng.choice(["morning", "afternoon", "night"])
    return [
        f"General housekeeping walk-through completed for the {shift} shift; no issues to report."
    ]


@train_template("unknown")
def _u2(rng: random.Random) -> list[Segment]:
    minutes = rng.choice(["a couple of", "a few", "several"])
    return [
        f"Canteen queue ran long during the break; {minutes} operators returned to "
        "the floor a little late."
    ]


@train_template("unknown")
def _u3(rng: random.Random) -> list[Segment]:
    area = rng.choice(["store entrance", "loading bay", "car park", "main gate"])
    return [f"Heavy rain this afternoon; checked for leaks near the {area}, no material affected."]


@train_template("unknown")
def _u4(rng: random.Random) -> list[Segment]:
    ref = FAKE_ORDER_REF
    where = rng.choice(
        [
            "the morning meeting",
            "a printed pick list",
            "an old whiteboard note",
            "yesterday's handover sheet",
        ]
    )
    return [
        f"Someone mentioned {ref} in {where} but nobody could find it in the "
        "system; likely a mis-write."
    ]


@train_template("unknown")
def _u5(rng: random.Random) -> list[Segment]:
    duration = rng.choice(["under three minutes", "about four minutes", "close to five minutes"])
    return [
        f"Fire drill held this morning; evacuation completed in {duration}, no follow-up actions."
    ]


@train_template("unknown")
def _u6(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    group = rng.choice(["a visitor group", "a student group", "a supplier delegation"])
    return [
        group.capitalize(),
        " toured near ",
        ent("LINE", line),
        " this afternoon; standard safety briefing given at the gate.",
    ]


@train_template("unknown")
def _u7(rng: random.Random) -> list[Segment]:
    duration = rng.choice(["briefly", "for a few seconds", "for about a minute"])
    return [
        f"Power flickered {duration} at shift start; no equipment reset was needed "
        "and production was not affected."
    ]


# --- unknown (test) ------------------------------------------------------


@test_template("unknown")
def _ut1(rng: random.Random) -> list[Segment]:
    tone = rng.choice(["routine", "quiet", "uneventful"])
    return [f"End-of-shift handover was {tone}; nothing notable to flag for the next supervisor."]


@test_template("unknown")
def _ut2(rng: random.Random) -> list[Segment]:
    place = rng.choice(["office block", "meeting room wing", "training room"])
    return [
        f"Air conditioning in the {place} was serviced today; no impact on the production floor."
    ]


@test_template("unknown")
def _ut3(rng: random.Random) -> list[Segment]:
    outcome = rng.choice(
        [
            "cleared as a supplier delivery",
            "cleared as a maintenance contractor",
            "cleared after checking with security",
        ]
    )
    return [f"Security noted an unfamiliar vehicle at the gate this morning; {outcome}."]


@test_template("unknown")
def _ut4(rng: random.Random) -> list[Segment]:
    ref = FAKE_ORDER_REF
    source = rng.choice(
        [
            "a printed sheet from last season",
            "an old training slide",
            "a filing cabinet label",
            "a retired planning board",
        ]
    )
    return [
        f"Reference {ref} came up on {source}; does not match any current order and can be ignored."
    ]


@test_template("unknown")
def _ut5(rng: random.Random) -> list[Segment]:
    count = rng.choice(["two units", "three units", "one unit"])
    return [
        f"Annual fire extinguisher check completed across the building; {count} "
        "tagged for refill next month."
    ]


@test_template("unknown")
def _ut6(rng: random.Random) -> list[Segment]:
    _, line = line_mention(rng)
    issue = rng.choice(["a loose floor tile", "a flickering light fitting", "a sticking door"])
    return ["Noticed ", issue, " near ", ent("LINE", line), "; reported to facilities for repair."]


@test_template("unknown")
def _ut7(rng: random.Random) -> list[Segment]:
    when = rng.choice(
        ["most of the morning shift", "the first two hours", "part of the afternoon shift"]
    )
    return [
        f"Water cooler on the ground floor was out of order for {when}; fixed "
        "before the next break."
    ]


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------


def generate_split(
    templates_by_label: dict[str, list[TemplateFn]],
    *,
    id_prefix: str,
    per_label: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    counter = 1
    for label in LABELS:
        templates = templates_by_label[label]
        produced = 0
        attempts = 0
        while produced < per_label:
            attempts += 1
            if attempts > per_label * 200:
                raise RuntimeError(
                    f"could not generate {per_label} unique notes for label {label!r}"
                )
            template = rng.choice(templates)
            parts = template(rng)
            text, entities = render(parts)
            if text in seen_texts:
                continue
            seen_texts.add(text)
            note_id = f"n-{id_prefix}-{counter:03d}"
            counter += 1
            produced += 1
            notes.append({"id": note_id, "text": text, "label": label, "entities": entities})
    return notes


def enforce_train_test_separation(
    train_notes: list[dict[str, Any]],
    test_notes: list[dict[str, Any]],
    templates_by_label: dict[str, list[TemplateFn]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    train_token_sets = [token_set(n["text"]) for n in train_notes]
    fixed: list[dict[str, Any]] = []
    seen_texts = {n["text"] for n in train_notes} | {n["text"] for n in test_notes}
    for note in test_notes:
        tokens = token_set(note["text"])
        max_sim = max((jaccard_similarity(tokens, t) for t in train_token_sets), default=0.0)
        if max_sim < JACCARD_MAX:
            fixed.append(note)
            continue
        # Regenerate this note from a different template in the same label.
        label = note["label"]
        templates = templates_by_label[label]
        for _ in range(200):
            template = rng.choice(templates)
            parts = template(rng)
            text, entities = render(parts)
            if text in seen_texts:
                continue
            new_tokens = token_set(text)
            new_sim = max(
                (jaccard_similarity(new_tokens, t) for t in train_token_sets), default=0.0
            )
            if new_sim < JACCARD_MAX:
                seen_texts.add(text)
                fixed.append({**note, "text": text, "entities": entities})
                break
        else:
            raise RuntimeError(
                f"could not regenerate a sufficiently distinct note for {note['id']}"
            )
    return fixed


def write_jsonl(path: Path, notes: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for note in notes:
            f.write(json.dumps(note, ensure_ascii=False))
            f.write("\n")


def main() -> None:
    rng = random.Random(RANDOM_SEED)  # noqa: S311 -- deterministic dataset generation, not crypto

    train_notes = generate_split(
        TRAIN_TEMPLATES, id_prefix="train", per_label=NOTES_PER_LABEL_TRAIN, rng=rng
    )
    test_notes = generate_split(
        TEST_TEMPLATES, id_prefix="test", per_label=NOTES_PER_LABEL_TEST, rng=rng
    )
    test_notes = enforce_train_test_separation(train_notes, test_notes, TEST_TEMPLATES, rng)

    write_jsonl(TRAIN_PATH, train_notes)
    write_jsonl(TEST_PATH, test_notes)

    print(f"Wrote {len(train_notes)} train notes to {TRAIN_PATH.relative_to(REPO_ROOT)}")
    print(f"Wrote {len(test_notes)} test notes to {TEST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
