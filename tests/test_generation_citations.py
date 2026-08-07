"""Pure parser tests from SPEC-005 AC-5 and AC-7a. No DB, no provider."""

import uuid

from rag_qa.generation.citations import AnswerParser, ParsedAnswer, parse_answer
from rag_qa.generation.types import (
    Citation,
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
    )


CHUNKS = [_chunk(i) for i in range(1, 9)]  # eight chunks, markers [1]..[8]


def _feed_in_slices(raw: str, size: int) -> AnswerParser:
    parser = AnswerParser(CHUNKS)
    for start in range(0, len(raw), size):
        parser.feed(raw[start : start + size])
    parser.finish()
    return parser


def _fields(parsed: ParsedAnswer) -> tuple[Verdict, str, tuple[Citation, ...], tuple[int, ...]]:
    """The four fields these tests were written against.

    `parse_answer` returns a `ParsedAnswer` since SPEC-005 KD-7 amendment 1 added
    `provisional_verdict` and `verdict_reconciled`; a six-tuple is not a readable
    return type. Tests that are ABOUT the two new fields read them by name.
    """
    return parsed.verdict, parsed.text, parsed.citations, parsed.dropped_markers


# --- AC-5: citation resolution ------------------------------------------------


def test_markers_resolve_to_chunks_in_order() -> None:
    verdict, text, citations, dropped = _fields(
        parse_answer("ANSWERED\nProviders must comply [1]. Oversight is required [3].", CHUNKS)
    )
    assert verdict is Verdict.ANSWERED
    assert text == "Providers must comply [1]. Oversight is required [3]."
    assert [c.marker for c in citations] == [1, 3]
    assert citations[0].chunk_id == CHUNKS[0].chunk_id
    assert citations[1].section_path == "Doc › Article 3"
    assert dropped == ()


def test_repeated_marker_cited_once() -> None:
    _, _, citations, _ = _fields(parse_answer("ANSWERED\nA [2]. B [2]. C [2].", CHUNKS))
    assert [c.marker for c in citations] == [2]


def test_adjacent_markers() -> None:
    _, text, citations, _ = _fields(parse_answer("ANSWERED\nBoth apply [1][2].", CHUNKS))
    assert text == "Both apply [1][2]."
    assert [c.marker for c in citations] == [1, 2]


def test_out_of_range_marker_stripped_and_recorded() -> None:
    _, text, citations, dropped = _fields(parse_answer("ANSWERED\nSee [9] and also [1].", CHUNKS))
    assert text == "See  and also [1]."  # [9] removed, nothing rendered in its place
    assert [c.marker for c in citations] == [1]
    assert dropped == (9,)


def test_zero_marker_is_out_of_range() -> None:
    _, _, citations, dropped = _fields(parse_answer("ANSWERED\nSee [0].", CHUNKS))
    assert citations == ()
    assert dropped == (0,)


def test_non_marker_brackets_are_literal_text() -> None:
    _, text, citations, _ = _fields(parse_answer("ANSWERED\nArray[abc] and [] and [1].", CHUNKS))
    assert text == "Array[abc] and [] and [1]."
    assert [c.marker for c in citations] == [1]


def test_trailing_open_bracket_flushed_as_text() -> None:
    _, text, _, _ = _fields(parse_answer("ANSWERED\nUnfinished [12", CHUNKS))
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

    _, text, _, _ = _fields(parse_answer(raw, CHUNKS))
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
    verdict, text, _, _ = _fields(parse_answer("INSUFFICIENT_EVIDENCE   \nNothing here.", CHUNKS))
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
    verdict, text, _, _ = _fields(parse_answer("Here is the answer:\nArticle 6 applies.", CHUNKS))
    assert verdict is Verdict.ERROR
    # Text still flows through so the bad response is logged for debugging.
    assert "Article 6 applies." in text


def test_missing_newline_long_response_is_error_but_keeps_text() -> None:
    raw = "ANSWERED " + "x" * 300
    verdict, text, _, _ = _fields(parse_answer(raw, CHUNKS))
    assert verdict is Verdict.ERROR
    assert "x" * 300 in text


# --- SPEC-005 Key decision 7 amendment 1: the trailing verdict -----------------
#
# **Mutation-verified**, each applied to `src/rag_qa/generation/citations.py`
# and reverted, with what actually failed:
#   - `verdict` returns the header, ignoring `_final_verdict` -> 3 fail.
#   - the trailing token is emitted as text instead of swallowed -> 2 fail.
#   - the FIRST standalone token wins instead of the last -> 1 fails (the
#     self-correction case, which is the shape v1 actually produced).
#   - `_could_begin_a_verdict` drops its prefix check, so any line is held ->
#     **survived the content tests**, because holding a line until its newline
#     preserves `text` exactly and only destroys streaming responsiveness. Caught
#     by adding the no-newline test; a mutation that is invisible to every
#     assertion about content is a missing assertion about timing.
#   - `finish()` stops checking `_line` -> the no-trailing-newline test fails.
#   - `provisional_verdict` returns the authoritative verdict -> 2 fail.


def test_the_trailing_token_overrides_the_header() -> None:
    """The v1 failure, exactly: header says ANSWERED, body declines.

    13 of 20 unanswerable questions did this under v1, and only the header
    existed, so `verdict` was wrong and nothing recorded that it was.
    """
    parsed = parse_answer(
        "ANSWERED\nThe excerpts do not give a figure [1].\nINSUFFICIENT_EVIDENCE\n", CHUNKS
    )
    assert parsed.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert parsed.provisional_verdict is Verdict.ANSWERED
    assert parsed.verdict_reconciled is True
    assert parsed.text == "The excerpts do not give a figure [1].\n"


def test_agreement_is_not_reconciliation() -> None:
    """Both tokens present and equal: nothing was overridden, and saying it was
    would inflate the disagreement rate this field exists to measure."""
    parsed = parse_answer("ANSWERED\nArticle 6 applies [1].\nANSWERED\n", CHUNKS)
    assert parsed.verdict is Verdict.ANSWERED
    assert parsed.verdict_reconciled is False
    assert parsed.text == "Article 6 applies [1].\n"


def test_a_missing_trailing_token_falls_back_to_the_header() -> None:
    """v1's shape must still parse: the header is all there is."""
    parsed = parse_answer("ANSWERED\nArticle 6 applies [1].", CHUNKS)
    assert parsed.verdict is Verdict.ANSWERED
    assert parsed.verdict_reconciled is False


def test_a_trailing_token_without_a_final_newline_still_counts() -> None:
    """The authoritative verdict must not depend on whether the provider ended
    the stream with a newline."""
    parsed = parse_answer("ANSWERED\nNothing here.\nINSUFFICIENT_EVIDENCE", CHUNKS)
    assert parsed.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert parsed.text == "Nothing here.\n"


def test_prose_beginning_with_a_token_is_prose() -> None:
    """The case v1 produced repeatedly and the reason only a BARE line counts.

    `INSUFFICIENT_EVIDENCE - the excerpts are...` is an answer, not a control
    token, and swallowing it would delete the sentence the reader needs.
    """
    raw = "ANSWERED\nINSUFFICIENT_EVIDENCE - the excerpts are from the 10-K [1].\n"
    parsed = parse_answer(raw, CHUNKS)
    assert parsed.text == "INSUFFICIENT_EVIDENCE - the excerpts are from the 10-K [1].\n"
    assert parsed.verdict is Verdict.ANSWERED, "no BARE trailing token, so the header stands"


def test_the_last_standalone_token_wins() -> None:
    """A2a and A5a both corrected themselves mid-answer under v1. The verdict
    the model ends on is the one it holds."""
    raw = "ANSWERED\nFirst take.\nANSWERED\nOn reflection, no.\nINSUFFICIENT_EVIDENCE\n"
    parsed = parse_answer(raw, CHUNKS)
    assert parsed.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert parsed.text == "First take.\nOn reflection, no.\n"


def test_streaming_and_non_streaming_agree_on_the_trailing_token() -> None:
    """AC-7 holds across the amendment: the same bytes, split anywhere, give the
    same text and the same authoritative verdict."""
    raw = "ANSWERED\nNo figure is given [2].\nINSUFFICIENT_EVIDENCE\n"
    reference = parse_answer(raw, CHUNKS)
    for split in range(1, len(raw)):
        parser = AnswerParser(CHUNKS)
        events = parser.feed(raw[:split])
        events.extend(parser.feed(raw[split:]))
        events.extend(parser.finish())
        deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
        assert deltas == reference.text, f"split at {split}"
        assert parser.verdict is reference.verdict, f"split at {split}"
        assert parser.text == reference.text, f"split at {split}"


def test_the_corrective_verdict_event_is_emitted_only_on_disagreement() -> None:
    """A client that rendered on the provisional frame must be told to correct;
    a client that was already right must not get a redundant frame."""
    for raw, expected in (
        ("ANSWERED\nNo figure [1].\nINSUFFICIENT_EVIDENCE\n", 1),
        ("ANSWERED\nArticle 6 applies [1].\nANSWERED\n", 0),
    ):
        parser = AnswerParser(CHUNKS)
        parser.feed(raw)
        parser.finish()
        assert parser.verdict_reconciled is bool(expected), raw


def test_body_text_streams_without_waiting_for_a_newline() -> None:
    """The trailing-token lookahead must not turn the stream line-buffered.

    Holding a line until its newline would preserve `text` **exactly** — which
    is why the content assertions above cannot see it — while destroying the
    property the whole verdict-first design exists for. The hold is bounded by
    the longest verdict token, and released the moment the line stops being a
    viable prefix of one.
    """
    parser = AnswerParser(CHUNKS)
    events = parser.feed("ANSWERED\nHello, no newline yet")
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello, no newline yet"
    # A line that IS a viable prefix is held, and only that far.
    parser = AnswerParser(CHUNKS)
    held = parser.feed("ANSWERED\nANSWE")
    assert [e for e in held if isinstance(e, TextDelta)] == []
    released = parser.feed("R but not really\n")
    assert "".join(e.text for e in released if isinstance(e, TextDelta)) == (
        "ANSWER but not really\n"
    )
