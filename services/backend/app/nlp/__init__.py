"""Notes NLP: master-data entity extraction, note classification and
grounded, evidence-backed status summaries (task-18-brief.md).

See ``docs/architecture/nlp.md``.
"""

from __future__ import annotations

from app.nlp.classifier import (
    CLASSES,
    KeywordBaselineClassifier,
    TfidfNoteClassifier,
    get_default_classifier,
)
from app.nlp.entities import (
    AMBIGUOUS_ID,
    EntityExtractor,
    EntityMention,
    MasterData,
    load_master_data,
)
from app.nlp.summarize import (
    DISABLED_LABEL,
    FIXTURE_LABEL,
    REJECTED_LABEL,
    GroundedSummary,
    SummarySentence,
    grounded_summary,
    model_summary,
)

__all__ = [
    "AMBIGUOUS_ID",
    "CLASSES",
    "DISABLED_LABEL",
    "FIXTURE_LABEL",
    "REJECTED_LABEL",
    "EntityExtractor",
    "EntityMention",
    "GroundedSummary",
    "KeywordBaselineClassifier",
    "MasterData",
    "SummarySentence",
    "TfidfNoteClassifier",
    "get_default_classifier",
    "grounded_summary",
    "load_master_data",
    "model_summary",
]
