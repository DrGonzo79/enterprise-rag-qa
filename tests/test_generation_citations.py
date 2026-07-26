"""Pure parser tests from SPEC-005 AC-5 and AC-7a. No DB, no provider."""

import uuid

from rag_qa.generation.citations import AnswerParser, parse_answer
from rag_qa.generation.types import (
    CitationEvent,
    TextDelta,
    Verdict,
    VerdictEvent,
)
from rag_qa.retrieval.types import RetrievedChunk


def _chunk(index: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, f"chunk-{index}"),
        document_id=uuid.uuid5(uuid.NAMESPACE_DNS, "doc"),
        document_title=f"Document {index}",
        source_uri=f"synthetic://doc/{index}",
        doc_type="regulation",
        section_path=f"Doc › Article {index}",
        ordinal=index,
        text=f"Body of chunk {index}.",
        score=1.0 / index,
        vector_rank=index,
        fulltext_rank=None,
    )


CHUNKS = [_chunk(i) for i in range(1, 9)]  # eight chunks, markers [1]..[8]


def _feed_in_slices(raw: str, size: int) -> AnswerParser:
    parser = AnswerParser(CHUNKS)
    for start in range(0, len(raw), size):
        parser.feed(raw[start : start + size])
    parser.finish()
    return parser


# --- AC-5: citation resolution ------------------------------------------------


def test_markers_resolve_to_chunks_in_order() -> None:
    verdict, text, citations, dropped = parse_answer(
        "ANSWERED\nProviders must comply [1]. Oversight is required [3].", CHUNKS
    )
    assert verdict is Verdict.ANSWERED
    assert text == "Providers must comply [1]. Oversight is required [3]."
    assert [c.marker for c in citations] == [1, 3]
    assert citations[0].chunk_id == CHUNKS[0].chunk_id
    assert citations[1].section_path == "Doc › Article 3"
    assert dropped == ()


def test_repeated_marker_cited_once() -> None:
    _, _, citations, _ = parse_answer("ANSWERED\nA [2]. B [2]. C [2].", CHUNKS)
    assert [c.marker for c in citations] == [2]


def test_adjacent_markers() -> None:
    _, text, citations, _ = parse_answer("ANSWERED\nBoth apply [1][2].", CHUNKS)
    assert text == "Both apply [1][2]."
    assert [c.marker for c in citations] == [1, 2]


def test_out_of_range_marker_stripped_and_recorded() -> None:
    _, text, citations, dropped = parse_answer("ANSWERED\nSee [9] and also [1].", CHUNKS)
    assert text == "See  and also [1]."  # [9] removed, nothing rendered in its place
    assert [c.marker for c in citations] == [1]
    assert dropped == (9,)


def test_zero_marker_is_out_of_range() -> None:
    _, _, citations, dropped = parse_answer("ANSWERED\nSee [0].", CHUNKS)
    assert citations == ()
    assert dropped == (0,)


def test_non_marker_brackets_are_literal_text() -> None:
    _, text, citations, _ = parse_answer("ANSWERED\nArray[abc] and [] and [1].", CHUNKS)
    assert text == "Array[abc] and [] and [1]."
    assert [c.marker for c in citations] == [1]


def test_trailing_open_bracket_flushed_as_text() -> None:
    _, text, _, _ = parse_answer("ANSWERED\nUnfinished [12", CHUNKS)
    assert text == "Unfinished [12"


def test_marker_split_across_every_boundary() -> None:
    raw = "ANSWERED\nA claim [2] and another [7]."
    expected_text = "A claim [2] and another [7]."
    for size in range(1, len(raw) + 1):
        parser = _feed_in_slices(raw, size)
        assert parser.text == expected_text, f"slice size {size}"
        assert [c.marker for c in parser.citations] == [2, 7], f"slice size {size}"


def test_streaming_text_equals_non_streaming_text() -> None:
    raw = "ANSWERED\nFirst [1]. Second [4]. Third [4]."
    parser = AnswerParser(CHUNKS)
    emitted: list[str] = []
    for start in range(0, len(raw), 3):
        for event in parser.feed(raw[start : start + 3]):
            if isinstance(event, TextDelta):
                emitted.append(event.text)
    for event in parser.finish():
        if isinstance(event, TextDelta):
            emitted.append(event.text)

    _, text, _, _ = parse_answer(raw, CHUNKS)
    assert "".join(emitted) == text


# --- AC-7a: the verdict token never reaches the client ------------------------


def _assert_no_verdict_leak(deltas: list[str], expected_body: str) -> None:
    """Exact concatenation equality is the strong check: any leaked fragment of
    the verdict token, of any length, makes the emitted text differ from the
    body. Substring checks on short prefixes are useless — 'A' and 'I' occur in
    ordinary prose."""
    emitted = "".join(deltas)
    assert emitted == expected_body
    assert "ANSWERED" not in emitted
    assert "INSUFFICIENT_EVIDENCE" not in emitted


def test_verdict_never_leaks_at_any_split_point() -> None:
    for raw, body in (
        ("ANSWERED\nArticle 6 sets the rules [1].", "Article 6 sets the rules [1]."),
        # Body starting with the verdict's own first letter — the case a naive
        # prefix check would false-positive on and a truncating bug would pass.
        ("ANSWERED\nAnswering requires Article 6 [1].", "Answering requires Article 6 [1]."),
        ("INSUFFICIENT_EVIDENCE\nInsufficient detail here.", "Insufficient detail here."),
    ):
        verdict_line_len = raw.index("\n") + 1
        for split in range(1, verdict_line_len + 1):
            parser = AnswerParser(CHUNKS)
            deltas: list[str] = []
            for piece in (raw[:split], raw[split:]):
                for event in parser.feed(piece):
                    if isinstance(event, TextDelta):
                        deltas.append(event.text)
            for event in parser.finish():
                if isinstance(event, TextDelta):
                    deltas.append(event.text)

            _assert_no_verdict_leak(deltas, body)
            assert parser.verdict is not Verdict.ERROR


def test_verdict_event_precedes_all_text() -> None:
    parser = AnswerParser(CHUNKS)
    events = parser.feed("ANSWERED\nSome text [1].")
    events.extend(parser.finish())
    assert isinstance(events[0], VerdictEvent)
    assert events[0].verdict is Verdict.ANSWERED
    assert any(isinstance(e, TextDelta) for e in events)
    assert any(isinstance(e, CitationEvent) for e in events)


def test_first_chunk_contains_newline_and_body() -> None:
    parser = AnswerParser(CHUNKS)
    events = parser.feed("ANSWERED\nImmediate body [1].")
    events.extend(parser.finish())
    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    _assert_no_verdict_leak(deltas, "Immediate body [1].")


def test_trailing_spaces_before_newline_still_parse() -> None:
    verdict, text, _, _ = parse_answer("INSUFFICIENT_EVIDENCE   \nNothing here.", CHUNKS)
    assert verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert text == "Nothing here."


def test_stream_ending_mid_verdict_yields_error_and_no_text() -> None:
    parser = AnswerParser(CHUNKS)
    events = parser.feed("ANSWE")
    events.extend(parser.finish())
    assert parser.verdict is Verdict.ERROR
    assert parser.text == ""
    assert [e for e in events if isinstance(e, TextDelta)] == []


def test_malformed_verdict_line_is_error() -> None:
    verdict, text, _, _ = parse_answer("Here is the answer:\nArticle 6 applies.", CHUNKS)
    assert verdict is Verdict.ERROR
    # Text still flows through so the bad response is logged for debugging.
    assert "Article 6 applies." in text


def test_missing_newline_long_response_is_error_but_keeps_text() -> None:
    raw = "ANSWERED " + "x" * 300
    verdict, text, _, _ = parse_answer(raw, CHUNKS)
    assert verdict is Verdict.ERROR
    assert "x" * 300 in text
