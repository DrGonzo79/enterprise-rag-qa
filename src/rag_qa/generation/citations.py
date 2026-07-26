"""Incremental verdict + citation-marker parser (SPEC-005 Interface).

One state machine serves both the streaming and non-streaming paths, so
`Answer.text` is by construction exactly the concatenation of the emitted
`TextDelta`s (AC-7).

Two streaming hazards it exists to handle:

- The verdict token must never reach the client, not even a prefix of it, so it
  is buffered until the first newline (AC-7a).
- A citation marker can be split across provider chunks (`"…text ["` then
  `"2] more"`), so markers are buffered with bounded lookahead.
"""

import uuid
from collections.abc import Sequence
from enum import Enum, auto

from rag_qa.generation.prompt import ANSWERED_TOKEN, INSUFFICIENT_TOKEN
from rag_qa.generation.types import (
    AnswerEvent,
    Citation,
    CitationEvent,
    TextDelta,
    Verdict,
    VerdictEvent,
)
from rag_qa.retrieval.types import RetrievedChunk

MAX_MARKER_DIGITS = 6
# A verdict line longer than this is malformed. Bounded so a model that omits
# the token entirely doesn't buffer its whole answer.
MAX_VERDICT_LINE = 200

_VERDICT_TOKENS = {
    ANSWERED_TOKEN: Verdict.ANSWERED,
    INSUFFICIENT_TOKEN: Verdict.INSUFFICIENT_EVIDENCE,
}


class _State(Enum):
    VERDICT = auto()
    BODY = auto()


class AnswerParser:
    """Feed provider text in arbitrary slices; collect ordered answer events."""

    def __init__(self, chunks: Sequence[RetrievedChunk]) -> None:
        self._chunks = list(chunks)
        self._state = _State.VERDICT
        self._verdict: Verdict | None = None
        self._verdict_buffer = ""
        self._marker: str | None = None
        self._pending = ""  # text not yet flushed as a TextDelta
        self._text_parts: list[str] = []
        self._citations: list[Citation] = []
        self._seen_markers: set[int] = set()
        self._dropped: list[int] = []

    # --- results -------------------------------------------------------------

    @property
    def verdict(self) -> Verdict:
        return self._verdict if self._verdict is not None else Verdict.ERROR

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    @property
    def citations(self) -> tuple[Citation, ...]:
        return tuple(self._citations)

    @property
    def dropped_markers(self) -> tuple[int, ...]:
        return tuple(self._dropped)

    # --- feeding -------------------------------------------------------------

    def feed(self, text: str) -> list[AnswerEvent]:
        events: list[AnswerEvent] = []
        for char in text:
            if self._state is _State.VERDICT:
                events.extend(self._feed_verdict(char))
            else:
                events.extend(self._feed_body(char))
        events.extend(self._flush_pending())
        return events

    def finish(self) -> list[AnswerEvent]:
        """Flush trailing state at end of stream."""
        events: list[AnswerEvent] = []
        if self._state is _State.VERDICT:
            # Ended mid-verdict with no newline: the buffer holds a partial token,
            # never prose. Emit nothing rather than leak it as text (AC-7a).
            self._verdict = Verdict.ERROR
            self._verdict_buffer = ""
            return [VerdictEvent(Verdict.ERROR)]
        if self._marker is not None:
            # A trailing "[" or "[12" was never a marker; it is literal text.
            self._pending += "[" + self._marker
            self._marker = None
        events.extend(self._flush_pending())
        return events

    # --- internals -----------------------------------------------------------

    def _feed_verdict(self, char: str) -> list[AnswerEvent]:
        if char == "\n":
            token = self._verdict_buffer.strip()
            self._verdict = _VERDICT_TOKENS.get(token, Verdict.ERROR)
            self._verdict_buffer = ""
            self._state = _State.BODY
            return [VerdictEvent(self._verdict)]

        self._verdict_buffer += char
        if len(self._verdict_buffer) <= MAX_VERDICT_LINE:
            return []

        # No newline in a plausible verdict line: the model omitted the token.
        # Report ERROR but let the text through — an unparseable answer is still
        # worth logging for debugging (it lands in query_log.answer_text).
        self._verdict = Verdict.ERROR
        buffered, self._verdict_buffer = self._verdict_buffer, ""
        self._state = _State.BODY
        events: list[AnswerEvent] = [VerdictEvent(Verdict.ERROR)]
        for buffered_char in buffered:
            events.extend(self._feed_body(buffered_char))
        return events

    def _feed_body(self, char: str) -> list[AnswerEvent]:
        if self._marker is None:
            if char == "[":
                self._marker = ""
                return self._flush_pending()
            self._pending += char
            return []

        if char.isdigit() and len(self._marker) < MAX_MARKER_DIGITS:
            self._marker += char
            return []

        if char == "]" and self._marker:
            marker = int(self._marker)
            self._marker = None
            return self._resolve_marker(marker)

        # Not a marker after all ("[abc", "[]", "[1234567"): emit literally and
        # reprocess this character, which may itself open a new marker.
        self._pending += "[" + self._marker
        self._marker = None
        return self._feed_body(char)

    def _resolve_marker(self, marker: int) -> list[AnswerEvent]:
        if not 1 <= marker <= len(self._chunks):
            # Hallucinated index: strip it from the text so nothing renders a
            # citation that resolves to nothing, but record it — silent stripping
            # would hide that the prompt is producing bad markers (KD-9).
            self._dropped.append(marker)
            return []

        self._pending += f"[{marker}]"
        events = self._flush_pending()
        if marker in self._seen_markers:
            return events

        self._seen_markers.add(marker)
        chunk = self._chunks[marker - 1]
        citation = Citation(
            marker=marker,
            chunk_id=chunk.chunk_id,
            section_path=chunk.section_path,
            document_title=chunk.document_title,
            source_uri=chunk.source_uri,
        )
        self._citations.append(citation)
        events.append(CitationEvent(citation))
        return events

    def _flush_pending(self) -> list[AnswerEvent]:
        if not self._pending:
            return []
        text, self._pending = self._pending, ""
        self._text_parts.append(text)
        return [TextDelta(text)]


def parse_answer(
    raw: str, chunks: Sequence[RetrievedChunk]
) -> tuple[Verdict, str, tuple[Citation, ...], tuple[int, ...]]:
    """Non-streaming convenience over the same state machine."""
    parser = AnswerParser(chunks)
    parser.feed(raw)
    parser.finish()
    return parser.verdict, parser.text, parser.citations, parser.dropped_markers


def chunk_ids(chunks: Sequence[RetrievedChunk]) -> list[uuid.UUID]:
    return [chunk.chunk_id for chunk in chunks]
