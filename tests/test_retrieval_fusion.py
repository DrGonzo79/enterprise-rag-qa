"""Pure fusion and diversity-metric tests from SPEC-004 AC-2 and AC-9. No DB."""

import uuid

from rag_qa.retrieval.fusion import RRF_K, rrf_fuse
from rag_qa.retrieval.metrics import distinct_section_rate
from rag_qa.retrieval.search import CandidateRow
from rag_qa.retrieval.types import RetrievedChunk

DOC_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ff")


def _row(name: str, section: str = "Doc › Section") -> CandidateRow:
    """Deterministic chunk_id per name so ordering assertions are stable."""
    return CandidateRow(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        document_id=DOC_ID,
        document_title="Doc",
        source_uri="synthetic://doc",
        doc_type="regulation",
        section_path=section,
        ordinal=0,
        text=name,
    )


def _by_text(results: list[RetrievedChunk]) -> list[str]:
    return [c.text for c in results]


# --- AC-2: fusion math --------------------------------------------------------


def test_rrf_k_is_sixty() -> None:
    assert RRF_K == 60


def test_scores_are_exact_reciprocal_rank_sums() -> None:
    a, b, c = _row("a"), _row("b"), _row("c")
    fused = rrf_fuse([a, b], [b, c])
    scores = {chunk.text: chunk.score for chunk in fused}

    assert scores["a"] == 1 / 61  # vector rank 1 only
    assert scores["b"] == 1 / 62 + 1 / 61  # vector rank 2 + fts rank 1
    assert scores["c"] == 1 / 62  # fts rank 2 only


def test_both_lists_at_rank_five_beat_single_list_rank_one() -> None:
    """1/65 + 1/65 > 1/61 — the agreement premise RRF rests on, asserted."""
    assert 1 / 65 + 1 / 65 > 1 / 61

    agreed = _row("agreed")
    single = _row("single")
    # Disjoint filler, so only `agreed` appears in both lists.
    vector_filler = [_row(f"v{i}") for i in range(3)]
    fulltext_filler = [_row(f"t{i}") for i in range(4)]

    fused = rrf_fuse([single, *vector_filler, agreed], [*fulltext_filler, agreed])

    assert fused[0].text == "agreed"
    assert (fused[0].vector_rank, fused[0].fulltext_rank) == (5, 5)
    assert fused[0].score == 2 / 65
    assert fused[1].text == "single"
    assert fused[1].score == 1 / 61


def test_ranks_are_recorded_and_none_when_absent() -> None:
    a, b = _row("a"), _row("b")
    fused = {chunk.text: chunk for chunk in rrf_fuse([a], [b])}

    assert (fused["a"].vector_rank, fused["a"].fulltext_rank) == (1, None)
    assert (fused["b"].vector_rank, fused["b"].fulltext_rank) == (None, 1)


def test_tie_break_is_best_rank_then_chunk_id() -> None:
    """Equal scores (both rank-1 in one list each) break by best rank, then id."""
    a, b = _row("a"), _row("b")
    fused = rrf_fuse([a], [b])

    assert fused[0].score == fused[1].score
    assert [c.chunk_id for c in fused] == sorted([a.chunk_id, b.chunk_id], key=str)


def test_ordering_is_deterministic_across_calls() -> None:
    rows = [_row(f"r{i}") for i in range(10)]
    first = _by_text(rrf_fuse(rows[:6], rows[3:]))
    second = _by_text(rrf_fuse(rows[:6], rows[3:]))
    assert first == second


# --- AC-12(b)/(c) at the fusion level: degenerate lists ------------------------


def test_empty_fulltext_list_degrades_to_vector_order() -> None:
    rows = [_row(f"r{i}") for i in range(5)]
    fused = rrf_fuse(rows, [])

    assert _by_text(fused) == _by_text(rrf_fuse(rows, []))
    assert [c.text for c in fused] == [r.text for r in rows]
    assert all(c.fulltext_rank is None for c in fused)


def test_empty_vector_list_degrades_to_fulltext_order() -> None:
    rows = [_row(f"r{i}") for i in range(5)]
    fused = rrf_fuse([], rows)
    assert [c.text for c in fused] == [r.text for r in rows]
    assert all(c.vector_rank is None for c in fused)


def test_both_lists_empty_fuses_to_empty() -> None:
    assert rrf_fuse([], []) == []


# --- AC-9: diversity metric ----------------------------------------------------


def test_distinct_section_rate() -> None:
    chunks = rrf_fuse(
        [
            _row("a", "Doc › S1"),
            _row("b", "Doc › S1"),
            _row("c", "Doc › S2"),
            _row("d", "Doc › S2"),
            _row("e", "Doc › S2"),
            _row("f", "Doc › S3"),
            _row("g", "Doc › S3"),
            _row("h", "Doc › S3"),
        ],
        [],
    )
    assert len(chunks) == 8
    assert distinct_section_rate(chunks) == 3 / 8  # 0.375


def test_distinct_section_rate_empty_is_zero() -> None:
    assert distinct_section_rate([]) == 0.0


def test_distinct_section_rate_all_unique_is_one() -> None:
    chunks = rrf_fuse([_row(f"r{i}", f"Doc › S{i}") for i in range(4)], [])
    assert distinct_section_rate(chunks) == 1.0


def test_fusion_is_blind_to_fallback_provenance() -> None:
    """`via_fallback` is RECORDED, NEVER FUSED (SPEC-004 AC-12 amendment 6).

    Nothing may weight on it yet: whether a fallback candidate should be worth
    1/61 is SPEC-007 Key decision 12's question, and answering it inside the
    fusion rule would be exactly the reservation KD-12 holds. This asserts the
    output is identical with the flag set either way, so a future weighting
    cannot be introduced without this test going red and being argued for.
    """
    import dataclasses

    vector = [_row(f"v{i}") for i in range(3)]
    fulltext = [_row(f"v{i}") for i in (5, 1, 6)]
    plain = rrf_fuse(vector, fulltext)
    marked = rrf_fuse(vector, [dataclasses.replace(row, via_fallback=True) for row in fulltext])

    assert [(c.chunk_id, c.score, c.vector_rank, c.fulltext_rank) for c in plain] == [
        (c.chunk_id, c.score, c.vector_rank, c.fulltext_rank) for c in marked
    ]
