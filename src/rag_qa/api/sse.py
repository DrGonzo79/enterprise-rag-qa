"""Server-sent event framing (SPEC-006 KD-3, KD-4).

Frames are unnamed `data:` lines with a `type` discriminator rather than named
SSE events: with named events an EventSource client must addEventListener for
*each* type, so adding a type silently drops events for every client that has not
been updated. Unnamed frames all reach `onmessage`, and an unknown type is
visibly unknown.

The payload is JSON for a framing reason, not a convenience one: SSE terminates
a field at `\\n`, so a raw newline in answer prose would split one logical event
across two frames. `json.dumps` escapes newlines, making the framing correct by
construction.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from rag_qa.api.schemas import UsageOut
from rag_qa.generation.types import (
    AnswerEvent,
    CitationEvent,
    TextDelta,
    VerdictEvent,
)

HEARTBEAT_FRAME = ": keepalive\n\n"

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # A buffering proxy silently defeats streaming; this disables nginx's.
    "X-Accel-Buffering": "no",
}


def data_frame(payload: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def error_frame(code: str, message: str) -> str:
    """Terminal frame for a failure after the headers went out with a 200 —
    the status can no longer be changed, so the failure has to be in-band."""
    return data_frame({"type": "error", "code": code, "message": message})


def event_payload(event: AnswerEvent) -> dict[str, Any]:
    if isinstance(event, VerdictEvent):
        return {"type": "verdict", "verdict": str(event.verdict)}
    if isinstance(event, TextDelta):
        return {"type": "text", "text": event.text}
    if isinstance(event, CitationEvent):
        return {
            "type": "citation",
            "marker": event.citation.marker,
            "chunk_id": str(event.citation.chunk_id),
            "section_path": event.citation.section_path,
            "document_title": event.citation.document_title,
            "source_uri": event.citation.source_uri,
        }
    return {
        "type": "complete",
        "verdict": str(event.answer.verdict),
        "dropped_markers": list(event.answer.dropped_markers),
        "usage": UsageOut.build(event.answer).model_dump(),
    }


async def with_heartbeats(frames: AsyncIterator[str], interval: float) -> AsyncIterator[str]:
    """Emit a comment frame whenever the source is idle longer than `interval`.

    Not decorative: the verdict token is buffered until the model's first newline
    and thinking runs before it (SPEC-005 KD-6), so a healthy stream can be silent
    for seconds. An idle-timeout proxy would otherwise kill it.
    """
    iterator = frames.__aiter__()
    while True:
        pending = asyncio.ensure_future(iterator.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=interval)
                if done:
                    break
                yield HEARTBEAT_FRAME
        except asyncio.CancelledError:
            pending.cancel()
            raise
        try:
            yield pending.result()
        except StopAsyncIteration:
            return
