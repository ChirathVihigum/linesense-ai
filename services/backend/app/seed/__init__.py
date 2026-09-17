"""Deterministic synthetic seed data (Task 6).

``vocabulary.py`` and ``identities.py`` are pure data (no I/O) shared with
the synthetic SOP corpus and the labelled NLP/IR evaluation datasets
(Task 16). ``generator.py`` and ``scenario.py`` build the full
demonstration dataset from that vocabulary (database I/O via
``seed_demo``); ``__main__.py`` is the ``python -m app.seed`` CLI.
"""
