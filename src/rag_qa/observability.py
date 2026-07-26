"""Log configuration — the missing half of SPEC-006 Key decision 5.

KD-5 argued at length for carrying the request id in a `ContextVar` with a
log-record factory rather than threading a parameter through two approved
interfaces, and AC-7 proved the id reaches every record emitted during a
request. What shipped alongside it was nothing that turns a record into output:
no formatter anywhere configured `request_id`, so the field was computed on every
record in the process and silently discarded, and `LOG_LEVEL` sat in
`.env.example` read by nothing. The seam existed and nothing consumed it.

This module is that consumer. It is top-level rather than under `api/` because
SPEC-007 and the ingestion CLI import the libraries directly, with no app in
sight, and need the same configuration. It is deliberately **not** named
`logging.py`: absolute imports would still resolve the standard library
correctly, and a reader would still have to stop and check.

**Configuration happens at the application edge and never at library import.**
A library that configures the root logger on import steals a decision from its
caller — it fights pytest's handler, duplicates uvicorn's, and makes the output
of `python -m rag_qa.ingest` depend on which module was imported first.
`configure_logging()` is explicit and idempotent, and things that own a process
call it.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

# Every attribute the logging module puts on a record itself. `Logger.makeRecord`
# raises KeyError when an `extra=` key collides with one of these, and the
# natural place to hit that is a diagnostic line inside an exception handler,
# where it turns a handled error into an unhandled one. The formatter treats
# everything outside this set as a structured field.
RESERVED_RECORD_FIELDS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}

DEFAULT_LEVEL = "INFO"
DEFAULT_FORMAT = "json"

# Marks the handler this module owns, so a second call reconfigures it rather
# than stacking a duplicate that doubles every line.
_OWNED = "_rag_qa_handler"


def _timestamp(created: float) -> str:
    stamped = datetime.fromtimestamp(created, UTC).isoformat(timespec="milliseconds")
    return stamped.replace("+00:00", "Z")


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in RESERVED_RECORD_FIELDS and key != "request_id"
    }


class JsonFormatter(logging.Formatter):
    """One line of JSON per record.

    `json.dumps` escapes newlines, so a message containing one cannot split a
    record across two lines — the same framing argument SPEC-006 KD-3 made for
    SSE, and correct by construction rather than by remembering to escape.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            # Empty outside any request, which is correct rather than a bug
            # (SPEC-006 KD-5) — there is no request.
            "request_id": getattr(record, "request_id", ""),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["error"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "unknown",
                "stack": self.formatException(record.exc_info),
            }
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable, for a developer at a terminal. Never the deployed mode."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = ""  # type: ignore[attr-defined]
        return super().format(record)


def configure_logging(
    *,
    level: str | None = None,
    fmt: str | None = None,
    stream: TextIO | None = None,
) -> logging.Handler:
    """Install one handler on the root logger. Idempotent.

    The root logger stays at WARNING while `rag_qa` takes `LOG_LEVEL`, so
    `LOG_LEVEL=DEBUG` produces this project's diagnostics rather than asyncio's
    and SQLAlchemy's.
    """
    resolved_level = (level or os.environ.get("LOG_LEVEL") or DEFAULT_LEVEL).upper()
    resolved_format = (fmt or os.environ.get("LOG_FORMAT") or DEFAULT_FORMAT).lower()
    formatter = TextFormatter() if resolved_format == "text" else JsonFormatter()

    root = logging.getLogger()
    handler = next((h for h in root.handlers if getattr(h, _OWNED, False)), None)
    if handler is None:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        setattr(handler, _OWNED, True)
        root.addHandler(handler)
    elif stream is not None:
        handler.setStream(stream)  # type: ignore[attr-defined]

    handler.setFormatter(formatter)
    handler.setLevel(logging.NOTSET)
    root.setLevel(logging.WARNING)
    logging.getLogger("rag_qa").setLevel(resolved_level)
    return handler
