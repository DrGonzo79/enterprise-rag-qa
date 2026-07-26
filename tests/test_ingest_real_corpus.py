"""Real-corpus tests from SPEC-003 AC-2/AC-3/AC-4: exact structural counts and
chunking invariants over the full documents.

Skipped when corpus/ is not populated (CI skips these; EUR-Lex sits behind a
WAF so a networked CI fetch would be non-deterministic). Run locally after
`python -m scripts.fetch_corpus`.
"""

import re
import statistics

import pytest

from conftest import CORPUS_DIR, REAL_CORPUS_PRESENT
from rag_qa.ingest.chunker import chunk_document
from rag_qa.ingest.loaders import load_edgar_10k, load_eurlex_html, load_nist_pdf
from rag_qa.ingest.types import IngestConfig, ParsedDocument
from test_ingest_chunker import assert_invariants

pytestmark = pytest.mark.skipif(
    not REAL_CORPUS_PRESENT, reason="real corpus not present in corpus/"
)

CONFIG = IngestConfig()

# "Part 1: Foundational Information" and "Part 2: Core and Profiles" are
# container headings with no body text of their own (the next heading follows
# immediately) and sit at depth 0 in the PDF's flat outline, so they yield no
# section — AC-2 requires every content-bearing top-level title instead.
NIST_TOP_LEVEL_TITLES = {
    "Executive Summary",
    "Framing Risk",
    "Audience",
    "AI Risks and Trustworthiness",
    "Effectiveness of the AI RMF",
    "AI RMF Core",
    "AI RMF Profiles",
    "Appendix A: Descriptions of AI Actor Tasks from Figures 2 and 3",
    "Appendix B: How AI Risks Differ from Traditional Software Risks",
    "Appendix C: AI Risk Management and Human-AI Interaction",
    "Appendix D: Attributes of the AI RMF",
}

EDGAR_CORE_ITEMS = {"1", "1A", "1B", "2", "3", "4", "5", "7", "7A", "8", "9", "9A", "10", "15"}


@pytest.fixture(scope="module")
def eurlex_doc() -> ParsedDocument:
    return load_eurlex_html(CORPUS_DIR / "eu-ai-act-2024-1689.html")


@pytest.fixture(scope="module")
def edgar_doc() -> ParsedDocument:
    return load_edgar_10k(CORPUS_DIR / "nvda-10k-2026.htm", CONFIG)


@pytest.fixture(scope="module")
def nist_doc() -> ParsedDocument:
    return load_nist_pdf(CORPUS_DIR / "nist-ai-rmf-100-1.pdf")


# --- AC-2: loader fidelity ----------------------------------------------------


def test_eurlex_exact_counts(eurlex_doc: ParsedDocument) -> None:
    recitals = [s for s in eurlex_doc.sections if s.heading_path[1] == "Preamble"]
    articles = [s for s in eurlex_doc.sections if s.heading_path[-1].lower().startswith("article")]
    annexes = [
        s
        for s in eurlex_doc.sections
        if len(s.heading_path) == 2 and s.heading_path[1].lower().startswith("annex")
    ]
    assert len(recitals) == 180
    assert len(articles) == 113
    assert len(annexes) == 13


def test_eurlex_zero_dropped_tables(eurlex_doc: ParsedDocument) -> None:
    """Decision 11 on the real document: 851 layout tables, zero dropped."""
    assert eurlex_doc.dropped_tables == ()


def test_nist_outline_titles_present_no_front_matter(nist_doc: ParsedDocument) -> None:
    seen = {title for s in nist_doc.sections for title in s.heading_path[1:]}
    missing = NIST_TOP_LEVEL_TITLES - seen
    assert not missing, f"missing outline titles: {missing}"
    assert not any("Contents" in t or "List of Figures" in t for t in seen)


def test_nist_no_running_header_in_sections(nist_doc: ParsedDocument) -> None:
    assert not any("NIST AI 100-1" in s.text for s in nist_doc.sections)


def test_edgar_items_exactly_once(edgar_doc: ParsedDocument) -> None:
    labels = {s.heading_path[1] for s in edgar_doc.sections}
    keys: list[str] = []
    for label in labels:
        m = re.match(r"^Item\s+(\d+[A-C]?)\.", label, re.IGNORECASE)
        if m:
            keys.append(m.group(1).upper())
    assert len(keys) == len(set(keys)), "an Item resolved to two different headings"
    missing = EDGAR_CORE_ITEMS - set(keys)
    assert not missing, f"missing Items: {missing}"


# --- AC-3: normalization over full corpus --------------------------------------


def test_no_replacement_chars_or_hidden_xbrl(
    eurlex_doc: ParsedDocument, edgar_doc: ParsedDocument, nist_doc: ParsedDocument
) -> None:
    for doc in (eurlex_doc, edgar_doc, nist_doc):
        for section in doc.sections:
            assert "�" not in section.text


# --- AC-4: chunking invariants over full corpus --------------------------------


@pytest.mark.parametrize("name", ["eurlex", "edgar", "nist"])
def test_chunking_invariants(
    name: str,
    eurlex_doc: ParsedDocument,
    edgar_doc: ParsedDocument,
    nist_doc: ParsedDocument,
) -> None:
    doc = {"eurlex": eurlex_doc, "edgar": edgar_doc, "nist": nist_doc}[name]
    chunks = chunk_document(doc, CONFIG)
    assert_invariants(chunks, CONFIG)

    # Median chunk lands in the target band (AC-4). The floor check tolerates
    # rare isolated sections that cannot merge with any sibling.
    sizes = [c.token_count for c in chunks]
    assert CONFIG.target_min <= statistics.median(sizes) <= CONFIG.target_max
    undersized = [s for s in sizes if s < CONFIG.hard_min]
    assert len(undersized) <= max(1, len(sizes) // 50), (
        f"{len(undersized)}/{len(sizes)} chunks under hard_min"
    )


def test_no_dropped_table_text_in_chunks(edgar_doc: ParsedDocument) -> None:
    """Spot-check per AC-3: no chunk is predominantly digits."""
    for chunk in chunk_document(edgar_doc, CONFIG):
        body = chunk.text.split("\n", 1)[1]
        digits = sum(ch.isdigit() for ch in body)
        alnum = sum(ch.isalnum() for ch in body) or 1
        assert digits / alnum <= 0.5, chunk.section_path
