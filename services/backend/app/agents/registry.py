"""The agents the executor may run, keyed by protocol recipient.

Empty until Task 13 registers the RM and planning agents; tests register
fakes through :func:`register_agent` and restore the registry afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from app.agents.base import BaseAgent

AGENTS: dict[str, type[BaseAgent]] = {}


def register_agent(name: str, agent_class: type[BaseAgent]) -> None:
    AGENTS[name] = agent_class


def get_agent(name: str) -> type[BaseAgent] | None:
    return AGENTS.get(name)


@contextmanager
def temporary_agent(name: str, agent_class: type[BaseAgent]) -> Iterator[None]:
    """Register ``agent_class`` for the duration of a block (tests only)."""
    previous = AGENTS.get(name)
    AGENTS[name] = agent_class
    try:
        yield
    finally:
        if previous is None:
            AGENTS.pop(name, None)
        else:
            AGENTS[name] = previous
