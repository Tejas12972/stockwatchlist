"""Logging setup.

Two formats: readable text for a terminal, single-line JSON for a deployment
where logs are shipped somewhere that parses them. Chosen with
`OPTIONS_LOG_FORMAT`.

Request IDs matter more than they look here. A single chain request fans out
into several vendor calls, retries and solver warnings; without a correlating id
those lines interleave with every other request in the log and a throttling
incident becomes impossible to reconstruct after the fact.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from typing import Any

__all__ = ["configure_logging", "request_id_var", "RequestIdFilter"]

# Set per request by the middleware; read by the log filter on every record.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # json.dumps, not an f-string: a vendor error message containing a quote
        # would otherwise produce a line no parser can read.
        return json.dumps(payload, default=str)


def configure_logging(log_format: str = "text", level: int = logging.INFO) -> None:
    """Install a single stdout handler. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())

    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root = logging.getLogger()
    # Replace rather than append, so repeated calls (reload, tests) do not
    # produce duplicated lines.
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; let them propagate to ours instead so
    # access logs carry the request id too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
