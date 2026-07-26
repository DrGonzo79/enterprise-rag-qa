"""Chunker tests from SPEC-003 AC-4 (invariants) on controlled inputs."""

import itertools
import logging

import pytest

from rag_qa.ingest.chunker import (
    BREADCRUMB_SEPARATOR,
    chunk_document,
    count_tokens,
    sentence_units,
)
from rag_qa.ingest.types import ChunkDraft, IngestConfig, ParsedDocument, Section

CONFIG = IngestConfig()


def _doc(sections: list[Section]) -> ParsedDocument:
    return ParsedDocument(
        source_uri="synthetic://doc",
        title="Synthetic",
        raw_bytes=b"synthetic",
        sections=tuple(sections),
    )


def _sentence(i: int, pad_words: int = 40) -> str:
    filler = " ".join(f"word{i}x{j}" for j in range(pad_words))
    return f"Sentence number {i} states that {filler} ends here."


def _big_section(path: tuple[str, ...], n_sentences: int) -> Section:
    return Section(heading_path=path, text=" ".join(_sentence(i) for i in range(n_sentences)))


def assert_invariants(chunks: list[ChunkDraft], config: IngestConfig = CONFIG) -> None:
    """Shared AC-4 assertions, also used by the real-corpus tests."""
    assert chunks, "no chunks produced"
    for chunk in chunks:
        assert chunk.token_count <= config.target_max, chunk.section_path
        assert chunk.token_count == count_tokens(chunk.text)
        assert chunk.text.startswith(chunk.section_path + "\n")
        assert BREADCRUMB_SEPARATOR in chunk.section_path


def test_small_sections_pack_together() -> None:
    """Sibling sections under target_min merge into one chunk (recital-style)."""
    sections = [
        Section(("Doc", "Preamble", f"Recital ({i})"), f"Recital {i} has one short sentence.")
        for i in range(1, 7)
    ]
    chunks = chunk_document(_doc(sections), CONFIG)
    assert len(chunks) == 1
    assert_invariants(chunks)
    # Merged coverage is visible in the breadcrumb leaf.
    assert "Recital (1) – Recital (6)" in chunks[0].section_path


def test_sections_with_different_parents_never_merge() -> None:
    """Cross-parent packing never happens for viable chunks; only the rescue
    merge (below hard_min) may cross parents."""
    sections = [
        Section(("Doc", "Part 1", "A"), " ".join(_sentence(i) for i in range(4))),
        Section(("Doc", "Part 2", "B"), " ".join(_sentence(i + 10) for i in range(4))),
    ]
    chunks = chunk_document(_doc(sections), CONFIG)
    assert len(chunks) == 2
    assert all(c.token_count >= CONFIG.hard_min for c in chunks)


def test_tiny_isolated_section_rescue_merges_across_parents() -> None:
    """An isolated sub-hard_min section ("Item 1B. None.") joins a neighbor;
    the breadcrumb becomes the common-ancestor range."""
    sections = [
        Section(("Doc", "Item 1A. Risks", "Sub"), " ".join(_sentence(i) for i in range(4))),
        Section(("Doc", "Item 1B. Unresolved"), "None."),
    ]
    chunks = chunk_document(_doc(sections), CONFIG)
    assert len(chunks) == 1
    assert chunks[0].section_path == "Doc › Item 1A. Risks – Item 1B. Unresolved"
    assert chunks[0].token_count >= CONFIG.hard_min


def test_oversized_section_splits_with_overlap() -> None:
    """A section over target_max splits at sentence boundaries; continuation
    chunks re-open with >= overlap_ratio * target_max tokens of trailing
    whole sentences."""
    section = _big_section(("Doc", "Chapter", "Article 3"), 40)
    chunks = chunk_document(_doc([section]), CONFIG)
    assert len(chunks) >= 2
    assert_invariants(chunks)

    threshold = int(CONFIG.overlap_ratio * CONFIG.target_max)
    all_units = sentence_units(section.text)
    for prev, nxt in itertools.pairwise(chunks):
        prev_body = prev.text.split("\n", 1)[1]
        next_body = nxt.text.split("\n", 1)[1]
        prev_units = sentence_units(prev_body)
        next_units = sentence_units(next_body)
        # Boundaries land on sentence boundaries (AC-4).
        assert prev_units[0] in all_units and prev_units[-1] in all_units
        # Overlap: the next chunk starts with trailing sentences of prev.
        overlap = [u for u in next_units if u in prev_units]
        assert overlap == prev_units[-len(overlap) :]
        assert sum(count_tokens(u) for u in overlap) >= threshold


def test_no_overlap_across_section_boundaries() -> None:
    """Two full siblings chunk separately; the second chunk repeats nothing."""
    a = _big_section(("Doc", "Part", "Alpha"), 12)
    b = Section(("Doc", "Part", "Beta"), "Beta begins fresh with its own single sentence.")
    chunks = chunk_document(_doc([a, b]), CONFIG)
    beta_chunks = [c for c in chunks if "Beta" in c.section_path and "Alpha" not in c.section_path]
    for chunk in beta_chunks:
        assert "Sentence number" not in chunk.text


def test_giant_sentence_hard_split_logged(caplog: pytest.LogCaptureFixture) -> None:
    giant = "tokenword " * 2000  # one "sentence", no terminal punctuation
    section = Section(("Doc", "Chapter", "Definitions"), giant)
    with caplog.at_level(logging.WARNING, logger="rag_qa.ingest.chunker"):
        chunks = chunk_document(_doc([section]), CONFIG)
    assert any("hard-splitting" in r.message for r in caplog.records)
    for chunk in chunks:
        assert chunk.token_count <= CONFIG.target_max


def test_line_boundaries_are_hard_boundaries() -> None:
    """Layout-table point rows (semicolon-terminated lines) are separate units,
    so a legal points list never registers as one giant sentence."""
    text = "\n".join(
        f"({chr(97 + i)}) point {i} lists obligations under Article {i};" for i in range(6)
    )
    units = sentence_units(text)
    assert len(units) == 6


def test_tail_chunk_meets_hard_min() -> None:
    """AC-4: no terminal fragment below hard_min; the tail steals sentences
    from its predecessor."""
    section = _big_section(("Doc", "Chapter", "Gamma"), 29)
    chunks = chunk_document(_doc([section]), CONFIG)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.token_count >= CONFIG.hard_min, chunk.section_path
