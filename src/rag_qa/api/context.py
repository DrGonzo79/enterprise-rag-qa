"""Request identifier: a ContextVar plus a log-record factory (SPEC-006 KD-5).

The id is *not* threaded through `Retriever.retrieve()` or `Generator.answer()`.
Both are transport-ignorant libraries that SPEC-007 calls directly with no HTTP
request in sight, and adding a parameter neither uses would change two approved
interfaces. A ContextVar set by middleware plus a log-record factory attaches the
id to every record emitted during the request — including the ones SPEC-004 and
SPEC-005 already emit — with zero changes to either.

The factory (rather than a logging.Filter) is deliberate: a filter attached to a
logger only sees records logged through *that* logger, so it would miss
`rag_qa.retrieval.service`; attaching to handlers means fighting whatever handler
is installed, including pytest's. Setting the field at record construction works
regardless of which logger emitted it or which handler consumes it.
"""

import logging
import re
import uuid
from contextvars import ContextVar
from typing import Any

REQUEST_ID_HEADER = "x-request-id"

# Inbound ids are attacker-controlled text headed for log records: newlines forge
# entries and unbounded length inflates every line of a request's logging (KD-6).
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

request_id_var: ContextVar[str] = ContextVar("rag_qa_request_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(raw: str | None) -> str:
    """A well-formed inbound id, else a fresh one.

    Malformed values are replaced silently rather than rejected: failing a
    request over a cosmetic header would be a worse outcome than ignoring it.
    """
    if raw is not None and _SAFE_REQUEST_ID.match(raw):
        return raw
    return new_request_id()


def current_request_id() -> str:
    return request_id_var.get()


# What the request turned out to be, filled in as it is discovered and read once
# by the completion record. A **mutable dict** in the ContextVar rather than a
# value, deliberately: `contextvars` copy at task creation, so SPEC-006's
# background SSE pump inherits a reference to this same dict and its verdict is
# visible to the middleware that created it. A plain value would be copied and
# the stream's outcome would never come back.
_outcome_var: ContextVar[dict[str, Any] | None] = ContextVar("rag_qa_outcome", default=None)


def new_outcome() -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    _outcome_var.set(outcome)
    return outcome


def record_outcome(**fields: Any) -> None:
    """Attach what is now known about the request. A no-op outside one."""
    outcome = _outcome_var.get()
    if outcome is not None:
        outcome.update(fields)


def install_log_record_factory() -> None:
    """Add `request_id` to every LogRecord. Idempotent."""
    existing = logging.getLogRecordFactory()
    if getattr(existing, "_rag_qa_request_id", False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = existing(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    factory._rag_qa_request_id = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
