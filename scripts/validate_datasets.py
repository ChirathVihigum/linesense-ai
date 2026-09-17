#!/usr/bin/env python3
"""Validator for the synthetic SOP corpus and labelled NLP/IR evaluation
datasets (Task 16: `data/synthetic/`, `data/eval/`).

Usage (as a script, from `services/backend` so `uv run` picks up the
backend's virtualenv and its `app` package is importable):

    cd services/backend && uv run python ../../scripts/validate_datasets.py

Every ``check_*``/``validate_*`` function returns a ``list[str]`` of human
readable problem descriptions (empty means "no problems found"), so this
module can also be imported directly by tests
(``services/backend/tests/unit/test_datasets.py``) to assert zero problems
against the real dataset, and to exercise individual checks against tiny
in-memory fixtures.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------
# Path setup: resolve everything relative to this file so the script
# behaves the same whether it is run directly or imported (e.g. via
# importlib from a test file with a different cwd).
# ---------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "services" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.seed import vocabulary as vocab  # noqa: E402

SOPS_DIR = REPO_ROOT / "data" / "synthetic" / "sops"
VERSIONS_DIR = SOPS_DIR / "versions"
ADVERSARIAL_DIR = REPO_ROOT / "data" / "synthetic" / "adversarial"
EVAL_DIR = REPO_ROOT / "data" / "eval"

NOTES_TRAIN_PATH = EVAL_DIR / "notes_train.jsonl"
NOTES_TEST_PATH = EVAL_DIR / "notes_test.jsonl"
RETRIEVAL_QUESTIONS_PATH = EVAL_DIR / "retrieval_questions.jsonl"

MIN_NOTES_TRAIN = 150
MIN_NOTES_TEST = 110
MIN_RETRIEVAL_TEST = 40
MIN_RETRIEVAL_DEV = 10
MIN_RETRIEVAL_DOCS = 20
JACCARD_MAX = 0.8

DISCLAIMER_LINE = "> Synthetic demonstration document — not an official factory procedure."

VALID_DOC_TYPES = {"SOP", "QUALITY_POLICY", "IE_STANDARD", "OTHER"}
VALID_SCOPES = {"org", "KTN", "BYG"}
VALID_ROLES = {
    "org_admin",
    "supervisor",
    "planner",
    "storekeeper",
    "ie_engineer",
    "quality_manager",
    "viewer",
}

# The 30 required SOP slugs, in the order given by the task brief.
EXPECTED_SLUGS: list[str] = [
    "fabric-receiving-inspection",
    "material-lot-acceptance",
    "supermarket-replenishment",
    "reorder-point-policy",
    "material-reservation-policy",
    "material-issue-to-line",
    "thread-cone-control",
    "trims-and-accessories-control",
    "cutting-ticket-procedure",
    "line-loading-procedure",
    "style-changeover",
    "shift-calendar-and-breaks",
    "sam-definition-and-maintenance",
    "time-study-procedure",
    "cycle-time-outlier-handling",
    "line-balancing-method",
    "bottleneck-escalation",
    "skill-matrix-governance",
    "inline-inspection-procedure",
    "final-inspection-demo-policy",
    "defect-catalogue",
    "critical-defect-response",
    "quality-hold-and-release",
    "packing-and-shipment-readiness",
    "needle-and-metal-control",
    "machine-preventive-maintenance",
    "capacity-planning-rules",
    "due-date-risk-escalation",
    "worker-data-privacy",
    "ai-assistant-usage-policy",
]

KTN_SCOPE_SLUGS = {"line-loading-procedure", "style-changeover"}
BYG_SCOPE_SLUGS = {"shift-calendar-and-breaks"}
WORKER_DATA_PRIVACY_ACL = ["org_admin", "supervisor", "ie_engineer"]

NOTE_LABELS = {"planning", "materials", "ie", "quality", "unknown"}
ENTITY_LABELS = {"ORDER", "LINE", "STYLE", "MATERIAL", "OPERATION", "DEFECT"}
MIN_CLASS_FRACTION = 0.15
MIN_UNKNOWN_FRACTION = 0.08

# A "frame" is a note's text with every labelled entity span replaced by
# its label placeholder, lowercased, and whitespace-collapsed. Two notes
# built from the same underlying sentence template (with different entity
# values) normalize to the same frame; this catches disguised repetition
# that a raw-text or per-note check would miss.
MAX_FRAME_USES = 3
MIN_UNIQUE_FRAME_FRACTION = 0.6
MIN_UNIQUE_FRAMES_PER_LABEL = 20

DEFECT_CODE_RE = re.compile(r"\bDEF-[A-Z]{2,4}\b")
KNOWN_DEFECT_CODES = {code for code, _, _ in vocab.DEFECT_CATALOG}

KNOWN_ORDER_REFS = set(vocab.ALL_ORDER_REFS)
KNOWN_LINE_CODES = {code for code, _ in (*vocab.KTN_LINES, *vocab.BYG_LINES)}
KNOWN_LINE_NAMES_LOWER = {name.lower() for _, name in (*vocab.KTN_LINES, *vocab.BYG_LINES)}
KNOWN_STYLE_CODES = set(vocab.STYLE_CODES)
KNOWN_MATERIAL_CODES = {code for code, _, _ in vocab.MATERIALS}
KNOWN_MATERIAL_NAMES_LOWER = {name.lower() for _, name, _ in vocab.MATERIALS}
KNOWN_OPERATION_NAMES_LOWER = {name.lower() for name, _ in vocab.OPERATION_CATALOG}
KNOWN_DEFECT_NAMES_LOWER = {name.lower() for _, name, _ in vocab.DEFECT_CATALOG}

# Small blocklist of common personal given/family names. Notes must be
# pseudonymous shift observations, never naming individual workers.
NAME_BLOCKLIST = {
    "kamal",
    "nimal",
    "sunil",
    "priya",
    "kumari",
    "silva",
    "perera",
    "fernando",
    "gunawardena",
    "jayasinghe",
    "wickramasinghe",
    "john",
    "smith",
    "maria",
    "ahmed",
    "raj",
    "rajesh",
    "chamari",
    "dilani",
    "saman",
}


# ---------------------------------------------------------------------
# Front matter / markdown parsing
# ---------------------------------------------------------------------


class FrontMatterError(ValueError):
    """Raised when a document has no parseable front matter block."""


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into its YAML front matter dict and markdown body.

    Raises ``FrontMatterError`` if ``text`` does not start with a
    ``---``-delimited front matter block.
    """
    if not text.startswith("---\n"):
        raise FrontMatterError("document does not start with '---' front matter delimiter")
    end = text.find("\n---\n", 4)
    if end == -1:
        # Front matter may be the entire remainder if body is empty; handle
        # the ``---\n...\n---`` (no trailing newline) case too.
        if text.rstrip().endswith("---") and text.count("---") >= 2:
            end = text.rstrip().rfind("\n---")
            fm_text = text[4:end]
            body = ""
            front_matter = yaml.safe_load(fm_text) or {}
            return front_matter, body
        raise FrontMatterError("closing '---' front matter delimiter not found")
    fm_text = text[4:end]
    body = text[end + 5 :]
    front_matter = yaml.safe_load(fm_text) or {}
    if not isinstance(front_matter, dict):
        raise FrontMatterError("front matter did not parse to a mapping")
    return front_matter, body


def parse_markdown_body(body: str) -> tuple[list[str], list[str], int]:
    """Return ``(title_lines, section_headings, word_count)`` for a body.

    ``title_lines`` are lines starting with ``# `` (single hash); section
    headings are ``## `` lines with the ``## `` prefix stripped.
    Word count is computed over the whole body (title + disclaimer +
    sections), which is the same definition used when the corpus was
    authored.
    """
    lines = body.splitlines()
    title_lines = [line for line in lines if line.startswith("# ") and not line.startswith("## ")]
    section_headings = [line[3:].strip() for line in lines if line.startswith("## ")]
    word_count = len(body.split())
    return title_lines, section_headings, word_count


class Document:
    """A parsed SOP document (front matter + body)."""

    def __init__(self, path: Path, front_matter: dict[str, Any], body: str) -> None:
        self.path = path
        self.front_matter = front_matter
        self.body = body
        self.title_lines, self.sections, self.word_count = parse_markdown_body(body)

    @property
    def slug(self) -> str | None:
        slug = self.front_matter.get("slug")
        return str(slug) if slug is not None else None


def load_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(text)
    return Document(path, front_matter, body)


# ---------------------------------------------------------------------
# SOP corpus checks
# ---------------------------------------------------------------------


def check_document_front_matter(
    doc: Document, *, context: str, slug_must_match_filename: bool = True
) -> list[str]:
    """Field-level checks that apply to every SOP-style document."""
    problems: list[str] = []
    fm = doc.front_matter

    slug = fm.get("slug")
    if not slug or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(slug)):
        problems.append(f"{context}: slug {slug!r} is missing or not kebab-case")
    elif slug_must_match_filename and slug != doc.path.stem:
        problems.append(f"{context}: slug {slug!r} does not match file name {doc.path.stem!r}")

    doc_type = fm.get("doc_type")
    if doc_type not in VALID_DOC_TYPES:
        problems.append(f"{context}: doc_type {doc_type!r} not in {sorted(VALID_DOC_TYPES)}")

    scope = fm.get("scope")
    if scope not in VALID_SCOPES:
        problems.append(f"{context}: scope {scope!r} not in {sorted(VALID_SCOPES)}")

    acl = fm.get("acl", [])
    if not isinstance(acl, list):
        problems.append(f"{context}: acl must be a list, got {type(acl).__name__}")
    else:
        bad_roles = [role for role in acl if role not in VALID_ROLES]
        if bad_roles:
            problems.append(f"{context}: acl contains unknown roles {bad_roles}")

    version = fm.get("version")
    if not isinstance(version, int) or version < 1:
        problems.append(f"{context}: version {version!r} must be a positive integer")

    title = fm.get("title")
    if not title or not isinstance(title, str):
        problems.append(f"{context}: title is missing or not a string")

    return problems


def check_document_body(doc: Document, *, context: str) -> list[str]:
    problems: list[str] = []

    if len(doc.title_lines) != 1:
        problems.append(
            f"{context}: expected exactly one '# ' title line, found {len(doc.title_lines)}"
        )

    if not (3 <= len(doc.sections) <= 8):
        problems.append(f"{context}: expected 3-8 '##' sections, found {len(doc.sections)}")

    if not (350 <= doc.word_count <= 1100):
        problems.append(f"{context}: word count {doc.word_count} outside 350-1100 range")

    body_lines = [line for line in doc.body.splitlines() if line.strip()]
    disclaimer_ok = False
    if len(body_lines) >= 2 and body_lines[0].startswith("# "):
        disclaimer_ok = body_lines[1].strip() == DISCLAIMER_LINE
    if not disclaimer_ok:
        problems.append(
            f"{context}: missing required disclaimer line as the first line under the title"
        )

    for code in DEFECT_CODE_RE.findall(doc.body):
        if code not in KNOWN_DEFECT_CODES:
            problems.append(
                f"{context}: unknown defect code {code!r} referenced (not in DEFECT_CATALOG)"
            )

    return problems


def check_sop_corpus(sops_dir: Path = SOPS_DIR) -> list[str]:
    """Validate every top-level `data/synthetic/sops/*.md` file."""
    problems: list[str] = []

    if not sops_dir.is_dir():
        return [f"SOP directory {sops_dir} does not exist"]

    md_files = sorted(p for p in sops_dir.glob("*.md") if p.is_file())
    if len(md_files) != 30:
        problems.append(f"expected exactly 30 SOP files in {sops_dir}, found {len(md_files)}")

    seen_slugs: dict[str, Path] = {}
    for path in md_files:
        context = f"{path.relative_to(REPO_ROOT)}"
        try:
            doc = load_document(path)
        except (FrontMatterError, yaml.YAMLError) as exc:
            problems.append(f"{context}: {exc}")
            continue

        problems.extend(check_document_front_matter(doc, context=context))
        problems.extend(check_document_body(doc, context=context))

        slug = doc.slug or path.stem
        if slug in seen_slugs:
            problems.append(f"{context}: duplicate slug {slug!r} also used by {seen_slugs[slug]}")
        else:
            seen_slugs[slug] = path

        scope = doc.front_matter.get("scope")
        if slug in KTN_SCOPE_SLUGS and scope != "KTN":
            problems.append(f"{context}: slug {slug!r} must have scope KTN, found {scope!r}")
        elif slug in BYG_SCOPE_SLUGS and scope != "BYG":
            problems.append(f"{context}: slug {slug!r} must have scope BYG, found {scope!r}")
        elif slug not in KTN_SCOPE_SLUGS and slug not in BYG_SCOPE_SLUGS and scope != "org":
            problems.append(f"{context}: slug {slug!r} must have scope org, found {scope!r}")

        if slug == "worker-data-privacy" and doc.front_matter.get("acl") != WORKER_DATA_PRIVACY_ACL:
            problems.append(
                f"{context}: worker-data-privacy acl must be exactly {WORKER_DATA_PRIVACY_ACL}, "
                f"found {doc.front_matter.get('acl')!r}"
            )

        if (
            slug == "final-inspection-demo-policy"
            and doc.front_matter.get("doc_type") != "QUALITY_POLICY"
        ):
            problems.append(
                f"{context}: final-inspection-demo-policy must have doc_type QUALITY_POLICY"
            )

    expected = set(EXPECTED_SLUGS)
    found = set(seen_slugs)
    missing = expected - found
    extra = found - expected
    if missing:
        problems.append(f"missing required slugs: {sorted(missing)}")
    if extra:
        problems.append(f"unexpected extra slugs: {sorted(extra)}")

    return problems


def check_version_file(versions_dir: Path = VERSIONS_DIR, sops_dir: Path = SOPS_DIR) -> list[str]:
    """Validate `versions/fabric-receiving-inspection-v1.md`: it must be a
    superseded version 1 of the same slug, with different content than the
    active version 2 document."""
    problems: list[str] = []
    v1_path = versions_dir / "fabric-receiving-inspection-v1.md"
    v2_path = sops_dir / "fabric-receiving-inspection.md"

    if not v1_path.is_file():
        return [f"{v1_path.relative_to(REPO_ROOT)}: file does not exist"]
    if not v2_path.is_file():
        return [f"{v2_path.relative_to(REPO_ROOT)}: active version file does not exist"]

    context = f"{v1_path.relative_to(REPO_ROOT)}"
    try:
        v1 = load_document(v1_path)
    except (FrontMatterError, yaml.YAMLError) as exc:
        return [f"{context}: {exc}"]

    problems.extend(
        check_document_front_matter(v1, context=context, slug_must_match_filename=False)
    )
    problems.extend(check_document_body(v1, context=context))

    if v1.slug != "fabric-receiving-inspection":
        problems.append(f"{context}: slug must be 'fabric-receiving-inspection', found {v1.slug!r}")
    if v1.front_matter.get("version") != 1:
        problems.append(f"{context}: version must be 1, found {v1.front_matter.get('version')!r}")

    try:
        v2 = load_document(v2_path)
    except (FrontMatterError, yaml.YAMLError) as exc:
        problems.append(f"{v2_path.relative_to(REPO_ROOT)}: {exc}")
        return problems

    if v2.front_matter.get("version", 1) <= 1:
        problems.append(f"{v2_path.relative_to(REPO_ROOT)}: active version must be > 1")

    if v1.body.strip() == v2.body.strip():
        problems.append(
            f"{context}: body is identical to the active version; must state a superseded rule"
        )

    return problems


def check_adversarial_files(adversarial_dir: Path = ADVERSARIAL_DIR) -> list[str]:
    problems: list[str] = []

    injection_path = adversarial_dir / "injection-sop.md"
    xss_path = adversarial_dir / "xss-note.md"

    if not injection_path.is_file():
        problems.append(f"{injection_path.relative_to(REPO_ROOT)}: file does not exist")
    else:
        context = f"{injection_path.relative_to(REPO_ROOT)}"
        try:
            doc = load_document(injection_path)
            if doc.slug != "adversarial-injection-test":
                problems.append(
                    f"{context}: slug must be 'adversarial-injection-test', found {doc.slug!r}"
                )
            if doc.front_matter.get("scope") != "org":
                problems.append(f"{context}: scope must be 'org'")
            required_markers = [
                "SYSTEM:",
                "ignore all previous instructions",
                "delete_all_records",
                "00000000-0000-0000-0000-000000000000",
            ]
            for marker in required_markers:
                if marker not in doc.body:
                    problems.append(f"{context}: missing required injection marker {marker!r}")
        except (FrontMatterError, yaml.YAMLError) as exc:
            problems.append(f"{context}: {exc}")

    if not xss_path.is_file():
        problems.append(f"{xss_path.relative_to(REPO_ROOT)}: file does not exist")
    else:
        context = f"{xss_path.relative_to(REPO_ROOT)}"
        text = xss_path.read_text(encoding="utf-8")
        for marker in ["<script>alert('x')</script>", "<img src=x onerror=alert(1)>"]:
            if marker not in text:
                problems.append(f"{context}: missing required XSS marker {marker!r}")

    return problems


# ---------------------------------------------------------------------
# Notes datasets
# ---------------------------------------------------------------------


def token_set(text: str) -> set[str]:
    """Lowercased whitespace/punctuation-insensitive token set of ``text``."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _resolves_to_master_data(label: str, mention_text: str) -> bool:
    if label == "ORDER":
        return mention_text in KNOWN_ORDER_REFS
    if label == "LINE":
        return mention_text in KNOWN_LINE_CODES or mention_text.lower() in KNOWN_LINE_NAMES_LOWER
    if label == "STYLE":
        return mention_text in KNOWN_STYLE_CODES
    if label == "MATERIAL":
        return (
            mention_text in KNOWN_MATERIAL_CODES
            or mention_text.lower() in KNOWN_MATERIAL_NAMES_LOWER
        )
    if label == "OPERATION":
        return mention_text.lower() in KNOWN_OPERATION_NAMES_LOWER
    if label == "DEFECT":
        return (
            mention_text in KNOWN_DEFECT_CODES or mention_text.lower() in KNOWN_DEFECT_NAMES_LOWER
        )
    return False


def check_note_object(note: dict[str, Any], *, context: str) -> list[str]:
    """Validate a single parsed note object's shape, spans, and entity
    resolution against master-data vocabulary. Does not check dataset-wide
    distribution or cross-note rules."""
    problems: list[str] = []

    note_id = note.get("id")
    if not note_id or not isinstance(note_id, str):
        problems.append(f"{context}: missing/invalid 'id'")

    text = note.get("text")
    if not isinstance(text, str) or not text:
        problems.append(f"{context}: missing/invalid 'text'")
        return problems  # can't check entities without text

    label = note.get("label")
    if label not in NOTE_LABELS:
        problems.append(f"{context}: label {label!r} not in {sorted(NOTE_LABELS)}")

    entities = note.get("entities")
    if not isinstance(entities, list):
        problems.append(f"{context}: 'entities' must be a list")
        return problems

    for idx, ent in enumerate(entities):
        ent_context = f"{context} entity[{idx}]"
        if not isinstance(ent, dict):
            problems.append(f"{ent_context}: must be an object")
            continue
        ent_label = ent.get("label")
        ent_text = ent.get("text")
        start = ent.get("start")
        end = ent.get("end")
        if ent_label not in ENTITY_LABELS:
            problems.append(f"{ent_context}: label {ent_label!r} not in {sorted(ENTITY_LABELS)}")
        if not isinstance(start, int) or not isinstance(end, int) or start >= end or start < 0:
            problems.append(f"{ent_context}: invalid span start={start!r} end={end!r}")
            continue
        if end > len(text):
            problems.append(f"{ent_context}: span end {end} beyond text length {len(text)}")
            continue
        actual = text[start:end]
        if actual != ent_text:
            problems.append(
                f"{ent_context}: text[{start}:{end}]={actual!r} does not match "
                f"entity text {ent_text!r}"
            )
        if (
            isinstance(ent_label, str)
            and isinstance(ent_text, str)
            and ent_label in ENTITY_LABELS
            and not _resolves_to_master_data(ent_label, ent_text)
        ):
            problems.append(
                f"{ent_context}: {ent_label} mention {ent_text!r} does not resolve "
                "to known master data"
            )

    # No personal names anywhere in the note text.
    for token in token_set(text):
        if token in NAME_BLOCKLIST:
            problems.append(f"{context}: possible personal name {token!r} found in note text")

    return problems


def _parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    problems: list[str] = []
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records, [f"{path.relative_to(REPO_ROOT)}: file does not exist"]
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        context = f"{path.relative_to(REPO_ROOT)}:{i}"
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{context}: invalid JSON ({exc})")
            continue
        if not isinstance(obj, dict):
            problems.append(f"{context}: line is not a JSON object")
            continue
        obj["__context__"] = context
        records.append(obj)
    return records, problems


def check_label_distribution(notes: list[dict[str, Any]], *, context: str) -> list[str]:
    problems: list[str] = []
    total = len(notes)
    if total == 0:
        return [f"{context}: no notes to check distribution over"]
    counts: dict[str, int] = dict.fromkeys(NOTE_LABELS, 0)
    for note in notes:
        label = note.get("label")
        if label in counts:
            counts[label] += 1
    for label in NOTE_LABELS - {"unknown"}:
        fraction = counts[label] / total
        if fraction < MIN_CLASS_FRACTION:
            problems.append(
                f"{context}: label {label!r} fraction {fraction:.3f} "
                f"below minimum {MIN_CLASS_FRACTION}"
            )
    unknown_fraction = counts["unknown"] / total
    if unknown_fraction < MIN_UNKNOWN_FRACTION:
        problems.append(
            f"{context}: label 'unknown' fraction {unknown_fraction:.3f} "
            f"below minimum {MIN_UNKNOWN_FRACTION}"
        )
    return problems


def normalize_frame(note: dict[str, Any]) -> str:
    """Return `note`'s text with every labelled entity span replaced by its
    label placeholder, lowercased and whitespace-collapsed. Two notes built
    from the same underlying sentence template (only entity *values*
    differing) normalize to the same frame."""
    text = note.get("text", "")
    raw_entities = note.get("entities", [])
    entities = sorted(
        (e for e in raw_entities if isinstance(e, dict) and "start" in e and "end" in e),
        key=lambda e: e["start"],
    )
    parts: list[str] = []
    cursor = 0
    for ent in entities:
        start, end = ent["start"], ent["end"]
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text)):
            continue
        parts.append(text[cursor:start])
        parts.append(f"<{str(ent.get('label', '')).lower()}>")
        cursor = end
    parts.append(text[cursor:])
    frame = "".join(parts).lower()
    return re.sub(r"\s+", " ", frame).strip()


def check_frame_diversity(notes: list[dict[str, Any]], *, context: str) -> list[str]:
    """Detect disguised repetition: notes that differ only in entity values
    but share the same underlying sentence frame. Fails if any frame is
    used more than `MAX_FRAME_USES` times, or if fewer than
    `MIN_UNIQUE_FRAME_FRACTION` of the notes have a unique frame."""
    problems: list[str] = []
    total = len(notes)
    if total == 0:
        return problems
    frames = [normalize_frame(note) for note in notes]
    counts = Counter(frames)
    unique = len(counts)
    if unique < MIN_UNIQUE_FRAME_FRACTION * total:
        problems.append(
            f"{context}: only {unique} unique entity-normalized sentence frames out of "
            f"{total} notes (minimum {MIN_UNIQUE_FRAME_FRACTION * 100:.0f}%)"
        )
    for frame, count in counts.items():
        if count > MAX_FRAME_USES:
            problems.append(
                f"{context}: sentence frame used {count} times (max {MAX_FRAME_USES}): {frame!r}"
            )
    return problems


def check_frame_diversity_by_label(notes: list[dict[str, Any]], *, context: str) -> list[str]:
    """Per-label counterpart to `check_frame_diversity`: a split-wide
    diversity fraction can hide one label that is almost entirely built
    from a handful of frames while other labels carry the diversity. Each
    label must have at least `min(MIN_UNIQUE_FRAMES_PER_LABEL, its own
    note count)` unique entity-normalized frames."""
    problems: list[str] = []
    by_label: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        label = note.get("label")
        if label in NOTE_LABELS:
            by_label.setdefault(label, []).append(note)

    for label, label_notes in by_label.items():
        required = min(MIN_UNIQUE_FRAMES_PER_LABEL, len(label_notes))
        unique = len({normalize_frame(note) for note in label_notes})
        if unique < required:
            problems.append(
                f"{context}: label {label!r} has only {unique} unique entity-normalized "
                f"sentence frames out of {len(label_notes)} notes (minimum {required})"
            )
    return problems


def check_notes_file(path: Path, *, min_lines: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse and validate a notes JSONL file. Returns (valid_notes, problems)."""
    records, problems = _parse_jsonl(path)
    if problems and not records:
        return [], problems

    context_label = path.relative_to(REPO_ROOT)
    if len(records) < min_lines:
        problems.append(
            f"{context_label}: expected at least {min_lines} lines, found {len(records)}"
        )

    seen_ids: set[str] = set()
    valid_notes: list[dict[str, Any]] = []
    for note in records:
        ctx = note["__context__"]
        note_problems = check_note_object(note, context=ctx)
        problems.extend(note_problems)
        note_id = note.get("id")
        if isinstance(note_id, str):
            if note_id in seen_ids:
                problems.append(f"{ctx}: duplicate id {note_id!r}")
            seen_ids.add(note_id)
        if not note_problems:
            valid_notes.append(note)

    problems.extend(check_label_distribution(records, context=str(context_label)))
    problems.extend(check_frame_diversity(valid_notes, context=str(context_label)))
    problems.extend(check_frame_diversity_by_label(valid_notes, context=str(context_label)))

    return valid_notes, problems


def check_train_test_separation(
    train_notes: list[dict[str, Any]], test_notes: list[dict[str, Any]]
) -> list[str]:
    problems: list[str] = []
    train_token_sets = [(n.get("id"), token_set(n.get("text", ""))) for n in train_notes]
    for test_note in test_notes:
        test_id = test_note.get("id")
        test_tokens = token_set(test_note.get("text", ""))
        for train_id, train_tokens in train_token_sets:
            sim = jaccard_similarity(test_tokens, train_tokens)
            if sim >= JACCARD_MAX:
                problems.append(
                    f"notes_test note {test_id!r} is too similar (Jaccard={sim:.2f}) to "
                    f"notes_train note {train_id!r}"
                )
    return problems


# ---------------------------------------------------------------------
# Retrieval questions
# ---------------------------------------------------------------------


def _document_sections_by_slug(sops_dir: Path = SOPS_DIR) -> dict[str, list[str]]:
    sections_by_slug: dict[str, list[str]] = {}
    if not sops_dir.is_dir():
        return sections_by_slug
    for path in sops_dir.glob("*.md"):
        try:
            doc = load_document(path)
        except (FrontMatterError, yaml.YAMLError):
            continue
        slug = doc.slug or path.stem
        sections_by_slug[slug] = doc.sections
    return sections_by_slug


def check_relevant_reference(
    doc_slug: str, section: str, sections_by_slug: dict[str, list[str]], *, context: str
) -> list[str]:
    if doc_slug not in sections_by_slug:
        return [f"{context}: relevant doc_slug {doc_slug!r} does not exist"]
    if section not in sections_by_slug[doc_slug]:
        return [f"{context}: section {section!r} does not exist in document {doc_slug!r}"]
    return []


def check_retrieval_questions(
    path: Path = RETRIEVAL_QUESTIONS_PATH,
    sections_by_slug: dict[str, list[str]] | None = None,
) -> list[str]:
    if sections_by_slug is None:
        sections_by_slug = _document_sections_by_slug()

    records, problems = _parse_jsonl(path)
    if problems and not records:
        return problems

    context_label = path.relative_to(REPO_ROOT)
    counts = {"test": 0, "dev": 0}
    referenced_docs: set[str] = set()
    seen_ids: set[str] = set()

    for record in records:
        ctx = record["__context__"]
        qid = record.get("id")
        if not qid or not isinstance(qid, str):
            problems.append(f"{ctx}: missing/invalid 'id'")
        elif qid in seen_ids:
            problems.append(f"{ctx}: duplicate id {qid!r}")
        else:
            seen_ids.add(qid)

        split = record.get("split")
        if split not in {"test", "dev"}:
            problems.append(f"{ctx}: split {split!r} not in ('test', 'dev')")
        else:
            counts[split] += 1

        question = record.get("question")
        if not question or not isinstance(question, str):
            problems.append(f"{ctx}: missing/invalid 'question'")

        relevant = record.get("relevant")
        if not isinstance(relevant, list) or not relevant:
            problems.append(f"{ctx}: 'relevant' must be a non-empty list")
        else:
            for rel in relevant:
                if not isinstance(rel, dict):
                    problems.append(f"{ctx}: relevant entry must be an object")
                    continue
                doc_slug = rel.get("doc_slug")
                section = rel.get("section")
                if not doc_slug or not section:
                    problems.append(f"{ctx}: relevant entry missing doc_slug/section")
                    continue
                referenced_docs.add(doc_slug)
                problems.extend(
                    check_relevant_reference(doc_slug, section, sections_by_slug, context=ctx)
                )
                if (
                    isinstance(question, str)
                    and question.strip().lower() == str(section).strip().lower()
                ):
                    problems.append(
                        f"{ctx}: question must not simply copy the section heading text"
                    )

    if counts["test"] < MIN_RETRIEVAL_TEST:
        problems.append(
            f"{context_label}: expected at least {MIN_RETRIEVAL_TEST} 'test' questions, "
            f"found {counts['test']}"
        )
    if counts["dev"] < MIN_RETRIEVAL_DEV:
        problems.append(
            f"{context_label}: expected at least {MIN_RETRIEVAL_DEV} 'dev' questions, "
            f"found {counts['dev']}"
        )
    if len(referenced_docs) < MIN_RETRIEVAL_DOCS:
        problems.append(
            f"{context_label}: relevant questions span {len(referenced_docs)} documents, "
            f"expected at least {MIN_RETRIEVAL_DOCS}"
        )

    return problems


# ---------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------


def validate_all() -> list[str]:
    problems: list[str] = []
    problems.extend(check_sop_corpus())
    problems.extend(check_version_file())
    problems.extend(check_adversarial_files())

    train_notes, train_problems = check_notes_file(NOTES_TRAIN_PATH, min_lines=MIN_NOTES_TRAIN)
    problems.extend(train_problems)
    test_notes, test_problems = check_notes_file(NOTES_TEST_PATH, min_lines=MIN_NOTES_TEST)
    problems.extend(test_problems)
    if train_notes and test_notes:
        problems.extend(check_train_test_separation(train_notes, test_notes))

    problems.extend(check_retrieval_questions())

    return problems


def main() -> int:
    problems = validate_all()
    if problems:
        print(f"FAIL: {len(problems)} problem(s) found:\n")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: synthetic dataset validation passed with zero problems.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
