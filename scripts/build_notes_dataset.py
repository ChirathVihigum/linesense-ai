#!/usr/bin/env python3
"""Generator for the labelled shift-note NLP datasets
(`data/eval/notes_train.jsonl`, `data/eval/notes_test.jsonl`) used by
Task 16's NLP/IR evaluation.

Notes are assembled from a bank of hand-written sentence *templates* (full
sentences/fragments with `{ENTITY}` placeholders) plus vocabulary mentions
drawn from `app.seed.vocabulary`. Each label/split combination has at least
`MIN_TEMPLATES_PER_LABEL_SPLIT` (20) genuinely distinct templates —
different sentence structures, lengths, and entity counts (0-3), not the
same skeleton with only entity *values* swapped — and no single template is
used more than `MAX_USES_PER_TEMPLATE` (3) times within a split. Entity
character offsets are computed programmatically as the text is assembled
(`render_template`), never guessed by hand, so `text[start:end] ==
entity_text` holds by construction.

Train and test use **disjoint** template banks with different phrasing
conventions. A fixed random seed (`RANDOM_SEED`) makes generation
reproducible. Every generated test note's token-set Jaccard similarity is
checked against every already-generated train note during generation itself
(not as a post-hoc pass) and a candidate that is too similar is discarded
and re-drawn, so the committed test set never contains a near-duplicate of
a train note (see `scripts/validate_datasets.py::JACCARD_MAX`).

Run with: `python3 scripts/build_notes_dataset.py` (paths are resolved from
this file's location, so it works regardless of the invoking cwd).
"""

from __future__ import annotations

import json
import random
import re
import sys
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

MIN_TEMPLATES_PER_LABEL_SPLIT = 20
MAX_USES_PER_TEMPLATE = 3

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


def order_mention(rng: random.Random) -> str:
    return rng.choice(ALL_ORDERS)


def line_mention(rng: random.Random) -> str:
    _code, name = rng.choice(ALL_LINES)
    form = rng.choice(["code", "name", "name_lower"])
    if form == "code":
        return _code
    if form == "name":
        return name
    return name.lower()


def style_mention(rng: random.Random) -> str:
    return rng.choice(STYLES)


def material_mention(rng: random.Random) -> str:
    code, name, _unit = rng.choice(MATERIALS)
    form = rng.choice(["code", "name", "name_lower"])
    if form == "code":
        return code
    if form == "name":
        return name
    return name.lower()


def operation_mention(rng: random.Random) -> str:
    name, _skill = rng.choice(OPERATIONS)
    return name


def defect_mention(rng: random.Random) -> str:
    code, name, _severity = rng.choice(DEFECTS)
    return rng.choice([code, name])


_MENTION_FNS = {
    "ORDER": order_mention,
    "LINE": line_mention,
    "STYLE": style_mention,
    "MATERIAL": material_mention,
    "OPERATION": operation_mention,
    "DEFECT": defect_mention,
}

# Matches {ORDER}, {ORDER2}, {LINE}, {LINE2}, ... and the special
# {FAKE_ORDER} token (a literal, never-tagged order-shaped reference).
_TOKEN_RE = re.compile(r"\{(ORDER2?|LINE2?|STYLE2?|MATERIAL2?|OPERATION2?|DEFECT2?|FAKE_ORDER)\}")


def render_template(template: str, rng: random.Random) -> tuple[str, list[dict[str, Any]]]:
    """Render a template string into (text, entities).

    `{LABEL}` placeholders are replaced with a mention of that entity type
    and recorded as a labelled span; `{LABEL2}` is a second, distinct
    mention of the same type (used for templates that name two orders/
    lines/etc.); `{FAKE_ORDER}` inserts the literal not-a-real-order
    reference with no entity span at all.
    """
    text_parts: list[str] = []
    entities: list[dict[str, Any]] = []
    cursor = 0
    text_len = 0
    chosen_primary: dict[str, str] = {}

    for match in _TOKEN_RE.finditer(template):
        literal = template[cursor : match.start()]
        text_parts.append(literal)
        text_len += len(literal)
        cursor = match.end()

        token = match.group(1)
        if token == "FAKE_ORDER":  # noqa: S105 -- template placeholder name, not a secret
            text_parts.append(FAKE_ORDER_REF)
            text_len += len(FAKE_ORDER_REF)
            continue

        is_second = token.endswith("2")
        base = token[:-1] if is_second else token
        mention_fn = _MENTION_FNS[base]
        mention = mention_fn(rng)
        if is_second:
            for _ in range(10):
                if mention != chosen_primary.get(base):
                    break
                mention = mention_fn(rng)
        else:
            chosen_primary[base] = mention

        start = text_len
        text_parts.append(mention)
        text_len += len(mention)
        entities.append({"label": base, "text": mention, "start": start, "end": text_len})

    text_parts.append(template[cursor:])
    return "".join(text_parts), entities


# ---------------------------------------------------------------------
# Template banks: TRAIN_TEMPLATES[label] / TEST_TEMPLATES[label] are
# disjoint lists of distinct sentence templates (strings). Each mixes
# short fragments, one-clause sentences, multi-clause sentences, 0-3
# entity mentions, and (for a few) a subordinate mention from a different
# domain while the label's own topic stays dominant.
# ---------------------------------------------------------------------

TRAIN_TEMPLATES: dict[str, list[str]] = {
    "planning": [
        "{ORDER} is behind schedule on {LINE}; flagging for replan.",
        "{LINE} finished changeover early and is ready to start {STYLE} ahead of the loaded shift.",
        "Supervisor moved {ORDER} onto {LINE} after the earlier slot filled up sooner than "
        "expected.",
        "Queue review: {ORDER} has priority over {ORDER2} under earliest-due-date sequencing this "
        "shift.",
        "{LINE} is loaded with {STYLE} for the rest of the shift; no changeover planned before "
        "tomorrow.",
        "{ORDER} capacity check done; remaining units fit in today's slot with margin to spare.",
        "{LINE} has spare standard minutes this shift; planner asked to check if another order can "
        "be pulled forward.",
        "Loading plan for tomorrow confirmed; no changes needed across any line.",
        "Short on planner time this morning; sequencing review pushed to the afternoon.",
        "{ORDER} due date pulled forward at the customer's request; checking whether {LINE} can "
        "absorb it.",
        "Quick note: {STYLE} changeover on {LINE} slipped by half a shift, knock-on effect still "
        "being assessed.",
        "Three orders are competing for the same slot on {LINE} today; sequenced by due date.",
        "{ORDER} is split across two lines this week because of a tight due date; coordinating "
        "with both supervisors.",
        "Nothing to flag on the loading board; all lines running to plan.",
        "{ORDER} and {ORDER2} are both due the same week; the capacity plan is under review.",
        "Planner asked IE to confirm {LINE}'s balance before committing {STYLE} to next week's "
        "schedule.",
        "{ORDER}'s unscheduled units carried over to tomorrow; material readiness was the limiting "
        "factor.",
        "Sequencing board updated; {LINE} now runs {STYLE} a full day ahead of the original plan.",
        "Loading board clean for the week.",
        "{ORDER} confirmed against {LINE} for both shifts tomorrow; storekeeper notified for "
        "material readiness.",
        "Reviewed next week's style mix with IE; {STYLE} needs a longer changeover window on "
        "{LINE} than planned.",
        "{ORDER} moved ahead of schedule after an earlier order finished under its planned units.",
        "Planner flagged a capacity gap on {LINE} for the weekend shift; still working out "
        "coverage.",
        "{ORDER}'s due-date risk closed after {LINE} picked up the shortfall from yesterday.",
    ],
    "materials": [
        "Short delivery of {MATERIAL} received; {ORDER} may run short by end of shift.",
        "{MATERIAL} lot moved to quarantine pending inspection; storekeeper notified.",
        "Reserved {MATERIAL} for {ORDER} after checking the available balance this morning.",
        "Supermarket bin for {MATERIAL} hit minimum quantity; pull card raised to the store.",
        "{MATERIAL} issued to {LINE} against this morning's cutting ticket.",
        "Reorder point review flagged {MATERIAL}; requisition raised to cover the next 3 weeks.",
        "{MATERIAL} lot accepted after inspection; balance updated for planning.",
        "Store is tight on {MATERIAL} this week; watching it before it becomes a shortage.",
        "Nothing to report from the store today; all bins topped up.",
        "{MATERIAL} count came up short during the weekly stock check; recount scheduled for "
        "tomorrow.",
        "{MATERIAL} lot rejected, returned to supplier.",
        "Released the {MATERIAL} reservation held for {ORDER} after the order's quantity was "
        "revised down.",
        "{ORDER} and {ORDER2} are both drawing from the same {MATERIAL} lot; watching the balance "
        "closely.",
        "Excess {MATERIAL} returned from the cutting table and credited back to store stock.",
        "New delivery of {MATERIAL} arrived a day early; moved straight to quarantine for "
        "inspection.",
        "{MATERIAL} consumption on {LINE} is running above the expected rate for this style.",
        "Storekeeper flagged that {MATERIAL}'s lead time has stretched; reorder point under "
        "review.",
        "Nothing unusual on materials today, just routine counts.",
        "{ORDER} is on hold until the {MATERIAL} delivery clears inspection tomorrow.",
        "Bundled a small top-up of {MATERIAL} to {LINE} ahead of tomorrow's higher-volume run.",
        "{MATERIAL} lot number mismatch found at issue; corrected before it left the store.",
        "Store walk-through clean today.",
        "{ORDER} is short by a few units of {MATERIAL}; storekeeper chasing an emergency top-up.",
        "Checked {MATERIAL} against the reorder point this morning; still comfortably above it.",
    ],
    "ie": [
        "{OPERATION} on {LINE} is running slower than expected; worth a time study.",
        "{LINE}'s balance index came in below the usual range this shift.",
        "{OPERATION} flagged as the bottleneck for {STYLE} again today.",
        "Two cycle-time readings for {OPERATION} were flagged as outliers and are pending IE "
        "review.",
        "First-off check on {LINE} for {STYLE} passed; running at full speed now.",
        "Reassigned a second operator to {OPERATION} to relieve today's bottleneck.",
        "Time study for {STYLE} completed; ten cycles recorded per operation.",
        "Nothing unusual from IE today; all lines are tracking to their theoretical output.",
        "{OPERATION}'s cycle time improved slightly after yesterday's method change.",
        "Line balance review completed, no action needed.",
        "{LINE} is running {STYLE} well below its theoretical output; investigating why.",
        "Skill gap confirmed on {LINE}; a style needing {OPERATION} can't be loaded there yet.",
        "{OPERATION} on two different lines is showing the same slow pattern; may be a method "
        "issue rather than a line issue.",
        "No outliers found in today's cycle-time sample.",
        "{STYLE}'s theoretical output was recalculated after a change to {OPERATION}'s cycle time.",
        "IE observed {LINE} for an hour; {OPERATION} was confirmed as the constraint.",
        "A machine change on {OPERATION} is being trialled; too early to say if it helped.",
        "Bottleneck on {LINE} is unchanged from yesterday; escalation opened per procedure.",
        "Nothing to flag from this morning's line walk.",
        "{OPERATION} outlier traced to a bundle delay, not a genuine slow cycle.",
        "{LINE}'s balance index recovered after yesterday's operator reassignment.",
        "Reviewed {STYLE}'s SAM values with the line supervisor; all still look reasonable.",
        "Time study rescheduled to tomorrow.",
        "{OPERATION} is the recurring bottleneck across three separate style runs on {LINE} this "
        "month.",
    ],
    "quality": [
        "Repeated {DEFECT} found at {OPERATION} on {LINE}; inline inspector notified.",
        "FINAL inspection for {ORDER} passed with two minor defects noted.",
        "{ORDER} placed on quality hold after a {DEFECT} finding during inline inspection.",
        "Quality hold on {ORDER} released after corrective action confirmed and a fresh sample "
        "passed.",
        "{DEFECT} count is trending up this week; raised with the quality manager for review.",
        "{ORDER}'s sample size was short of eighty units; result recorded as insufficient sample, "
        "re-inspection scheduled.",
        "Metal detector on {LINE} calibrated at shift start; no issues found.",
        "Nothing to flag from today's inline rounds.",
        "FINAL inspection passed, no defects.",
        "{ORDER} failed FINAL inspection; defect count exceeded the policy's threshold.",
        "Needle breakage on {LINE} fully accounted for; machine cleared to resume.",
        "{DEFECT} traced back to {OPERATION}; operator re-briefed on the method.",
        "Second quality hold added to {ORDER} for a separate issue found this morning.",
        "Inline inspector flagged {DEFECT} on three consecutive samples from {OPERATION}.",
        "{ORDER} cleared FINAL inspection this afternoon and is now eligible for packing.",
        "Nothing unusual on quality today; all lines within normal defect rates.",
        "{DEFECT} finding on {LINE} confirmed as a one-off; no pattern found across the rest of "
        "the shift.",
        "Critical finding on {ORDER}: bundle isolated pending investigation.",
        "Metal detector recalibrated after a failed check.",
        "{ORDER} and {ORDER2} both held for the same {DEFECT} issue traced to one machine.",
        "Packing held for {ORDER} pending release of an earlier quality hold.",
        "Defect catalogue query: confirming {DEFECT} is the correct code before logging today's "
        "finding.",
        "{LINE} passed its first-off check for {STYLE} with no defects noted.",
        "Root cause for the recurring {DEFECT} on {LINE} traced to a worn attachment; replaced and "
        "retested.",
    ],
    "unknown": [
        "General housekeeping walk-through completed for the shift; no issues to report.",
        "Canteen queue ran long during the break; a few operators returned to the floor a little "
        "late.",
        "Heavy rain this afternoon; checked for leaks near the store entrance, no material "
        "affected.",
        "Someone mentioned {FAKE_ORDER} in the morning meeting but nobody could find it in the "
        "system; likely a mis-write.",
        "Fire drill held this morning; evacuation completed within the target time, no follow-up "
        "actions.",
        "A visitor group toured near {LINE} this afternoon; standard safety briefing given at the "
        "gate.",
        "Power flickered briefly at shift start; no equipment reset was needed and production was "
        "not affected.",
        "End-of-shift handover was routine; nothing notable to flag for the next supervisor.",
        "Air conditioning in the office block was serviced today; no impact on the production "
        "floor.",
        "Security noted an unfamiliar vehicle at the gate this morning; cleared as a supplier "
        "delivery.",
        "Annual fire extinguisher check completed across the building; 2 units tagged for refill.",
        "Old paperwork mentioning {ORDER} turned up during a desk clear-out; nothing to action, "
        "filed for records.",
        "Water cooler on the ground floor was out of order for most of the morning; fixed by early "
        "afternoon.",
        "A visitor at reception briefly asked about {ORDER}; redirected to the planning office.",
        "Parking area resurfacing finished a day ahead of schedule; no disruption to shift "
        "changeovers.",
        "Routine day, no incidents.",
        "First aid kit near {LINE} restocked during the routine monthly check.",
        "A training session on fire safety ran during the lunch break; well attended.",
        "Notice board near {LINE} was updated with this quarter's canteen menu; unrelated to "
        "production.",
        "Reference {FAKE_ORDER} came up again on an old printout; still not a real order in the "
        "system.",
        "Cleaning contractor visited the office wing this afternoon; production floor untouched.",
        "Signage near {LINE} was replaced after last week's storm damage; purely cosmetic.",
        "Bicycle rack near the main gate was repaired after last week's storm damage.",
        "Staff canteen menu changed for the week; no impact on shift timing.",
    ],
}

TEST_TEMPLATES: dict[str, list[str]] = {
    "planning": [
        "Planning note: reassigned {ORDER} to {LINE} for the remainder of today's run.",
        "{LINE}'s changeover to {STYLE} took noticeably longer than planned this morning.",
        "Due-date watch: {ORDER} will miss its date unless an extra shift slot opens up before "
        "Friday.",
        "Sequencing swap approved: {ORDER} now runs ahead of {ORDER2} per the supervisor's note.",
        "Checked {LINE}'s shift board; tomorrow's load is confirmed and no gaps remain.",
        "Loaded {ORDER} onto {LINE} a shift earlier than originally scheduled to cover a gap on "
        "another line.",
        "Style {STYLE}'s capacity plan was reviewed with IE; no changes needed for next week's "
        "loading.",
        "Board looks quiet today; every line is running to the plan drawn up yesterday.",
        "Weekend coverage is still unresolved for {LINE}; supervisor chasing a decision from "
        "planning.",
        "{ORDER} needs a second line to hit its date; splitting the run across {LINE} and another "
        "line.",
        "Loading board reviewed, no action needed.",
        "{ORDER} came off hold and went straight back onto {LINE}'s queue for this afternoon.",
        "{STYLE}'s changeover on {LINE} ran to plan for once; first-off passed on the first try.",
        "Two orders tied for the same due date this week; {ORDER} was given priority since it was "
        "booked in first.",
        "{LINE} is sitting idle for the next hour waiting on a decision from planning; following "
        "up now.",
        "{ORDER} was pulled off {LINE} temporarily to make room for an urgent style trial.",
        "Nothing new to report on the schedule; yesterday's plan is still holding.",
        "{ORDER} and {ORDER2} were resequenced this morning after a late input from the customer.",
        "{LINE}'s slot for tomorrow is fully booked with {STYLE}; no room for anything else right "
        "now.",
        "IE confirmed {LINE} can absorb the extra volume, so {ORDER} was added to tomorrow's plan.",
        "Schedule holding steady, no swaps made today.",
        "{ORDER} is waiting on a materials confirmation before it can be committed to {LINE}.",
        "Reworked this week's style sequence on {LINE} after yesterday's late changeover.",
        "{ORDER} finished ahead of plan; the freed-up time on {LINE} was handed to the next order "
        "in the queue.",
    ],
    "materials": [
        "Materials update: {ORDER} is on hold pending an incoming delivery of {MATERIAL}.",
        "Storekeeper rejected a lot of {MATERIAL} after finding it outside the acceptance "
        "threshold.",
        "Count discrepancy noted on {MATERIAL} during the weekly supermarket walk-through; recount "
        "scheduled.",
        "{LINE} requested an early top-up of {MATERIAL} ahead of tomorrow's higher-volume style.",
        "Consumption of {MATERIAL} this week is tracking above the usual rate; worth a "
        "reorder-point recheck.",
        "Material store quiet today, nothing to flag.",
        "{ORDER} was reassigned to a different {MATERIAL} lot after the original one failed "
        "inspection.",
        "Two lines are now drawing on the same {MATERIAL} bin; watching it so it doesn't run dry "
        "before the shift ends.",
        "{MATERIAL} arrived a day late; {ORDER} was pushed back half a shift as a result.",
        "Store confirmed the {MATERIAL} balance is healthy; no action needed this week.",
        "{ORDER} and {ORDER2} both need {MATERIAL} this week; store is checking if supply covers "
        "both.",
        "Supermarket card for {MATERIAL} was raised twice today; bin sizing may need review.",
        "Nothing new from the store; yesterday's counts still hold.",
        "The {MATERIAL} reservation for {ORDER} was released after a bill-of-materials correction "
        "came through.",
        "Cutting table returned an unusually large amount of unused {MATERIAL}; checking why.",
        "Store inspection passed, no issues.",
        "{ORDER} is the second order this week to be delayed by a {MATERIAL} shortage.",
        "Storekeeper walked the supermarket bins twice today after yesterday's miscount on "
        "{MATERIAL}.",
        "{MATERIAL}'s lead time was confirmed longer than expected; reorder point being "
        "recalculated.",
        "{LINE} ran short of {MATERIAL} mid-shift; covered from a nearby bin without stopping the "
        "line.",
        "Nothing to escalate on materials; routine day at the store.",
        "{ORDER} was cleared for cutting once its {MATERIAL} lot passed inspection this afternoon.",
        "Checked the {MATERIAL} balance against three open orders; coverage looks tight but "
        "workable.",
        "Store flagged a supplier packaging issue on the latest {MATERIAL} delivery; inspecting "
        "closely.",
    ],
    "ie": [
        "IE observed {OPERATION} on {LINE} and confirmed it as today's constraint operation.",
        "Bottleneck escalation opened for {LINE} after the balance index stayed low for a second "
        "consecutive shift.",
        "A method change was trialled on {OPERATION}; cycle time improved slightly but needs a "
        "full re-study to confirm.",
        "SAM for {OPERATION} under {STYLE} is being reviewed after a machine change last week.",
        "Skill gap noted on {LINE}; loading a style requiring that skill will need cross-training "
        "first.",
        "Outlier reading on {OPERATION} traced to a bundle delay, not a genuine slow cycle; "
        "excluded from the study.",
        "{STYLE}'s theoretical output was recalculated after yesterday's time study update.",
        "Line walk clean, nothing flagged for IE today.",
        "{LINE} is tracking close to its theoretical output for the third day running.",
        "Two operators were swapped between {OPERATION} and another step to see if the bottleneck "
        "moves.",
        "Nothing new to report from this week's time studies.",
        "{OPERATION}'s cycle time held steady even after the operator change on {LINE}.",
        "Bottleneck review closed, no further action needed.",
        "{STYLE} on {LINE} is now balanced to within a few seconds across every operation.",
        "IE flagged {OPERATION} for a re-study after three separate outlier readings this week.",
        "{LINE}'s balance index dropped sharply after this morning's changeover; still "
        "investigating.",
        "The machine type behind {OPERATION} is under review after two lines showed the same slow "
        "pattern.",
        "Nothing unusual in today's cycle-time sample.",
        "{OPERATION} bottleneck resolved by adding a second operator on {LINE}.",
        "SAM review complete, values confirmed unchanged.",
        "{STYLE}'s balance index came in higher than expected; double-checking the recorded cycle "
        "times.",
        "A recurring bottleneck at {OPERATION} across three lines is now being treated as a shared "
        "machine issue.",
        "{LINE}'s first-off check for {STYLE} failed; held at reduced speed pending a fix.",
        "IE closed out this week's time studies with no outstanding outlier reviews.",
    ],
    "quality": [
        "Critical finding: {DEFECT} detected on a unit from {ORDER}; bundle isolated pending "
        "investigation.",
        "{ORDER} passed FINAL inspection this afternoon and moved through to the packing queue.",
        "Root cause for the recurring {DEFECT} was traced back to {OPERATION}; operator re-briefed "
        "on the method.",
        "Second quality hold added to {ORDER} for a separate labelling issue found this morning.",
        "Needle breakage on {LINE} fully accounted for; no missing fragments, machine cleared to "
        "resume.",
        "Defect catalogue query: confirming {DEFECT} is the correct code for this morning's "
        "finding before logging it.",
        "Packing for {ORDER} is on hold until an earlier quality hold is released.",
        "Inline rounds clean today, nothing logged.",
        "{ORDER} failed its FINAL sample; re-inspection booked for tomorrow morning.",
        "Two consecutive {DEFECT} findings on {LINE} triggered a line stop for a quick check.",
        "Nothing to escalate from today's quality rounds.",
        "{DEFECT} rate on {LINE} is back within normal range after yesterday's fix.",
        "Metal detector calibration confirmed, no issues found.",
        "{ORDER} was released from hold after a fresh FINAL sample passed cleanly.",
        "Inline inspector rotated onto {LINE} this week; defect logging is consistent with the "
        "previous inspector.",
        "{STYLE}'s first-off on {LINE} flagged a {DEFECT}; held at reduced speed pending a fix.",
        "Nothing unusual to report from quality today.",
        "{ORDER} and {ORDER2} were both flagged for the same {DEFECT} traced to a single delivery "
        "of trims.",
        "Quality hold review completed, all clear.",
        "{DEFECT} finding closed out after the responsible machine was serviced.",
        "Sample size for {ORDER}'s FINAL inspection was short of the policy minimum; re-inspection "
        "scheduled.",
        "Quality manager reviewed this week's {DEFECT} trend; no action needed yet.",
        "{ORDER} was placed on hold pending confirmation that a {DEFECT} finding was isolated to "
        "one bundle.",
        "No critical defects this week.",
    ],
    "unknown": [
        "Handover to the next supervisor was straightforward; nothing worth flagging.",
        "Office block's air conditioning had its scheduled service; production floor was "
        "unaffected.",
        "Security noted an unfamiliar vehicle at the gate this morning; checked and cleared as a "
        "supplier delivery.",
        "Reference {FAKE_ORDER} came up on a printed sheet from last season; does not match any "
        "current order and can be ignored.",
        "Annual fire extinguisher check completed across the building; two units tagged for refill "
        "next month.",
        "Noticed a loose floor tile near {LINE}; reported to facilities for repair.",
        "Water cooler on the ground floor was out of order for most of the morning shift; fixed "
        "before the next break.",
        "Quiet day, nothing to report.",
        "A supplier delegation toured the site this afternoon; standard visitor briefing given at "
        "reception.",
        "Uneventful shift, nothing flagged.",
        "A reception query about {ORDER} turned out to be for a cancelled order from last season.",
        "Cleaning contractor serviced the training room today; production floor untouched.",
        "Nothing out of the ordinary across the site today.",
        "First aid kit near {LINE} restocked during this month's routine check.",
        "An old whiteboard note mentioning {FAKE_ORDER} was found during a desk clear-out; "
        "discarded as outdated.",
        "Car park resurfacing wrapped up a day early, so shift changeovers went ahead without any "
        "disruption.",
        "A courier asked for directions related to {ORDER} but had the wrong site entirely.",
        "Canteen supplier changed this week; no impact on break timing.",
        "Bicycle rack near the gate was repaired after last week's storm damage.",
        "Fire safety refresher training ran during the lunch break; well attended.",
        "Nothing to escalate from today's site walk-around.",
        "Meeting room wing had its carpets cleaned overnight; no disruption to the floor.",
        "Quiet shift, nothing further to add.",
        "Notice board near {LINE} updated with this month's safety statistics.",
    ],
}


def _validate_template_banks() -> None:
    for split_name, bank in (("train", TRAIN_TEMPLATES), ("test", TEST_TEMPLATES)):
        for label in LABELS:
            templates = bank.get(label, [])
            if len(set(templates)) != len(templates):
                raise ValueError(f"duplicate template string in {split_name}/{label}")
            if len(templates) < MIN_TEMPLATES_PER_LABEL_SPLIT:
                raise ValueError(
                    f"{split_name}/{label} has only {len(templates)} templates, "
                    f"need >= {MIN_TEMPLATES_PER_LABEL_SPLIT}"
                )
    for label in LABELS:
        overlap = set(TRAIN_TEMPLATES[label]) & set(TEST_TEMPLATES[label])
        if overlap:
            raise ValueError(f"train/test template overlap for label {label!r}: {overlap}")


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------


def generate_split(
    templates_by_label: dict[str, list[str]],
    *,
    id_prefix: str,
    per_label: int,
    rng: random.Random,
    against_token_sets: list[set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Generate `per_label` notes per label from `templates_by_label`.

    No template is used more than `MAX_USES_PER_TEMPLATE` times. If
    `against_token_sets` is given (the token sets of an already-generated
    opposite split), any candidate note whose Jaccard similarity to any of
    them is >= `JACCARD_MAX` is discarded and a different rendering/
    template is drawn instead — this is how train/test separation is
    enforced, as part of generation rather than as a post-hoc patch.
    """
    notes: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    counter = 1
    for label in LABELS:
        templates = templates_by_label[label]
        usage: dict[str, int] = dict.fromkeys(templates, 0)
        produced = 0
        attempts = 0
        max_attempts = per_label * 1000
        while produced < per_label:
            attempts += 1
            if attempts > max_attempts:
                raise RuntimeError(
                    f"could not generate {per_label} unique, sufficiently distinct notes "
                    f"for label {label!r} ({id_prefix}) after {max_attempts} attempts"
                )
            candidates = [t for t in templates if usage[t] < MAX_USES_PER_TEMPLATE]
            if not candidates:
                raise RuntimeError(
                    f"template capacity exhausted for label {label!r} ({id_prefix}): "
                    f"{len(templates)} templates * {MAX_USES_PER_TEMPLATE} uses "
                    f"< {per_label} needed"
                )
            template = rng.choice(candidates)
            text, entities = render_template(template, rng)
            if text in seen_texts:
                continue
            if against_token_sets is not None:
                tokens = token_set(text)
                if any(jaccard_similarity(tokens, t) >= JACCARD_MAX for t in against_token_sets):
                    continue
            seen_texts.add(text)
            usage[template] += 1
            note_id = f"n-{id_prefix}-{counter:03d}"
            counter += 1
            produced += 1
            notes.append({"id": note_id, "text": text, "label": label, "entities": entities})
    return notes


def write_jsonl(path: Path, notes: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for note in notes:
            f.write(json.dumps(note, ensure_ascii=False))
            f.write("\n")


def main() -> None:
    _validate_template_banks()
    rng = random.Random(RANDOM_SEED)  # noqa: S311 -- deterministic dataset generation, not crypto

    train_notes = generate_split(
        TRAIN_TEMPLATES, id_prefix="train", per_label=NOTES_PER_LABEL_TRAIN, rng=rng
    )
    train_token_sets = [token_set(n["text"]) for n in train_notes]
    test_notes = generate_split(
        TEST_TEMPLATES,
        id_prefix="test",
        per_label=NOTES_PER_LABEL_TEST,
        rng=rng,
        against_token_sets=train_token_sets,
    )

    write_jsonl(TRAIN_PATH, train_notes)
    write_jsonl(TEST_PATH, test_notes)

    print(f"Wrote {len(train_notes)} train notes to {TRAIN_PATH.relative_to(REPO_ROOT)}")
    print(f"Wrote {len(test_notes)} test notes to {TEST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
