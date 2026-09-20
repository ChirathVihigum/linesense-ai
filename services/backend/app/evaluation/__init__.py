"""LineSense evaluation harness (task-19-brief.md).

``python -m app.evaluation`` runs the retrieval, NLP, calculation, agent,
fairness and security evaluation sections against a dedicated database and
writes a versioned JSON document plus a Markdown rendering under
``docs/evaluation/results/``. See ``docs/evaluation/methodology.md`` for
what each section measures and, crucially, what it does not show.
"""

from __future__ import annotations
