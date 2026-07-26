"""Loader tests from SPEC-003 AC-2/AC-3 (synthetic fixtures; real-corpus
counterparts live in test_ingest_real_corpus.py)."""

from pathlib import Path

from conftest import SYNTH_EDGAR_BYTES, SYNTH_EURLEX, build_synth_pdf
from rag_qa.ingest.loaders import load_edgar_10k, load_eurlex_html, load_nist_pdf
from rag_qa.ingest.types import IngestConfig, ParsedDocument


def _eurlex(tmp_path: Path) -> ParsedDocument:
    path = tmp_path / "synth-eurlex.html"
    path.write_text(SYNTH_EURLEX, encoding="utf-8")
    return load_eurlex_html(path)


def _edgar(tmp_path: Path, config: IngestConfig | None = None) -> ParsedDocument:
    path = tmp_path / "synth-10k.htm"
    path.write_bytes(SYNTH_EDGAR_BYTES)
    return load_edgar_10k(path, config)


def _nist(tmp_path: Path) -> ParsedDocument:
    path = tmp_path / "synth-nist.pdf"
    path.write_bytes(build_synth_pdf())
    return load_nist_pdf(path)


# --- EUR-Lex -----------------------------------------------------------------


def test_eurlex_sections_and_headings(tmp_path: Path) -> None:
    doc = _eurlex(tmp_path)
    paths = [s.heading_path for s in doc.sections]

    assert ("EU AI Act", "Preamble", "Recital (1)") in paths
    assert ("EU AI Act", "Preamble", "Recital (2)") in paths
    # Article 1 sits in Chapter I (no section); Article 2 under Section 1.
    assert ("EU AI Act", "CHAPTER I — GENERAL PROVISIONS", "Article 1 — Subject matter") in paths
    assert (
        "EU AI Act",
        "CHAPTER I — GENERAL PROVISIONS",
        "SECTION 1 — Classification",
        "Article 2 — Scope",
    ) in paths
    # Annexes are segmented by oj-doc-ti headings (no eli ids).
    annexes = [p for p in paths if len(p) == 2 and p[1].startswith("Annex")]
    assert len(annexes) == 2
    # Citations (cit_*) and the footnote panel are discarded.
    joined = " ".join(s.text for s in doc.sections)
    assert "Having regard to the Treaty" not in joined
    assert "OJ L 97" not in joined


def test_eurlex_layout_table_linearized_not_duplicated(tmp_path: Path) -> None:
    doc = _eurlex(tmp_path)
    art1 = next(s for s in doc.sections if s.heading_path[-1].startswith("Article 1"))
    # Point rows read as "(a) <text>" lines, present exactly once.
    assert art1.text.count("(a) rules amending Regulations") == 1
    assert "Article 6(2)" in art1.text


def test_eurlex_never_drops_tables(tmp_path: Path) -> None:
    """Decision 11: the citation-dense point table exceeds the EDGAR digit
    threshold, yet the EUR-Lex loader must keep it and report zero drops."""
    doc = _eurlex(tmp_path)
    assert doc.dropped_tables == ()
    art1 = next(s for s in doc.sections if s.heading_path[-1].startswith("Article 1"))
    assert "No 300/2008" in art1.text


# --- EDGAR -------------------------------------------------------------------


def test_edgar_cp1252_decode(tmp_path: Path) -> None:
    doc = _edgar(tmp_path)
    joined = " ".join(s.text for s in doc.sections)
    assert "company’s markets" in joined
    assert "�" not in joined


def test_edgar_ix_header_stripped(tmp_path: Path) -> None:
    doc = _edgar(tmp_path)
    joined = " ".join(s.text for s in doc.sections)
    assert "HIDDEN-METADATA" not in joined
    # Inline ix facts stay part of their sentence.
    assert "130,497 million this year" in joined


def test_edgar_items_from_body_not_toc(tmp_path: Path) -> None:
    doc = _edgar(tmp_path)
    item_labels = {s.heading_path[1] for s in doc.sections}
    assert item_labels == {"Item 1. Business", "Item 1A. Risk Factors", "Item 2. Properties"}
    # The TOC's page-number cells never leak into section text.
    business = next(s for s in doc.sections if s.heading_path[1] == "Item 1. Business")
    assert "accelerated computing" in business.text


def test_edgar_bold_subheading_becomes_path_element(tmp_path: Path) -> None:
    doc = _edgar(tmp_path)
    subs = [s for s in doc.sections if len(s.heading_path) == 3]
    assert any(s.heading_path[2] == "Our Markets" for s in subs)


def test_edgar_numeric_table_dropped_narrative_kept(tmp_path: Path) -> None:
    doc = _edgar(tmp_path)
    assert len(doc.dropped_tables) == 1
    dropped = doc.dropped_tables[0]
    assert dropped.reason == "numeric_table_threshold"
    assert dropped.digit_ratio >= 0.5
    assert dropped.item == "Item 1A. Risk Factors"
    assert dropped.cell_count == 9

    joined = " ".join(s.text for s in doc.sections)
    assert "Gross margin" not in joined  # dropped table content absent
    assert "Common Stock | NVDA-S" in joined  # narrative table linearized


def test_edgar_threshold_config_respected(tmp_path: Path) -> None:
    lenient = IngestConfig(edgar_numeric_table_threshold=0.95)
    doc = _edgar(tmp_path, lenient)
    assert doc.dropped_tables == ()
    joined = " ".join(s.text for s in doc.sections)
    assert "Gross margin" in joined


# --- NIST PDF ----------------------------------------------------------------


def test_nist_outline_drives_sections(tmp_path: Path) -> None:
    doc = _nist(tmp_path)
    paths = [s.heading_path for s in doc.sections]
    assert ("NIST AI RMF 1.0", "Alpha Section") in paths
    assert ("NIST AI RMF 1.0", "Beta Section") in paths
    assert ("NIST AI RMF 1.0", "Beta Section", "Beta Child") in paths


def test_nist_running_header_stripped_and_dehyphenated(tmp_path: Path) -> None:
    doc = _nist(tmp_path)
    joined = " ".join(s.text for s in doc.sections)
    assert "SYNTH DOC HEADER" not in joined  # AC-3
    assert "benefits of synthetic fixtures" in joined  # AC-3 de-hyphenation
    assert "bene- fits" not in joined
