"""Structured (structlog) JSON logging configuration.

Never logs tokens, cookies, provider keys, full prompts, or document text:
see ``_redact_sensitive_keys`` below.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.api.middleware import trace_id_var
from app.settings import Settings

_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "api_key",
    "token",
    "password",
    "prompt",
    "document_text",
}


def _add_trace_id(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    trace_id = trace_id_var.get()
    if trace_id:
        event_dict.setdefault("trace_id", trace_id)
    return event_dict


def _redact_sensitive_keys(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            del event_dict[key]
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog for JSON output with trace IDs and redaction."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_trace_id,
            _redact_sensitive_keys,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        # False (structlog's own default): a logger obtained via
        # `structlog.get_logger(...)` at import time must pick up
        # `configure_logging()` being called again later (e.g. once per test
        # app) and must respect `structlog.testing.capture_logs()`, which
        # works by temporarily reconfiguring the processor chain. Caching
        # would bake in whichever processors were active the first time a
        # given logger name was used and ignore later reconfiguration.
        cache_logger_on_first_use=False,
    )
