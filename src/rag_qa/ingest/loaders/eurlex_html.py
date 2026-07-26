"""EU AI Act (EUR-Lex OJ HTML) loader: sectioning via eli-subdivision ids.

Measured (SPEC-003 Purpose): `eli-subdivision` divs with ids rct_1..rct_180
(recitals) and art_1..art_113 (articles); article titles in oj-ti-art +
oj-sti-art; chapters/sections in oj-ti-section-1/2; 13 annexes marked only by
oj-doc-ti headings (no eli ids); 851 layout tables carrying point lists —
linearized unconditionally, never dropped (Key decision 11).
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from rag_qa.ingest.normalize import normalize_lines, normalize_text
from rag_qa.ingest.types import ParsedDocument, Section

SOURCE_URI = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689"
TITLE = "Regulation (EU) 2024/1689 (Artificial Intelligence Act)"
DOC_LABEL = "EU AI Act"

_ANNEX_RE = re.compile(r"^ANNEX\s+[IVXLC]+$")


def _block_text(tag: Tag) -> str:
    """Text of a tag with block boundaries as newlines.

    Tables and paragraphs each become their own line so the chunker can treat
    layout-table point rows ("(a) ...") as atomic units.
    """
    parts: list[str] = []
    for block in tag.find_all(["p", "tr"]):
        # A <p> inside a <tr> is already covered by the row's own text, and a
        # nested table's rows are covered by the enclosing row (nested points).
        if block.find_parent("tr") is not None:
            continue
        text = normalize_text(block.get_text(" ", strip=True))
        if text:
            parts.append(text)
    if not parts:
        text = normalize_text(tag.get_text(" ", strip=True))
        return text
    return "\n".join(parts)


def _article_context(art: Tag) -> tuple[str, ...]:
    """Chapter (and section, when present) labels preceding this article."""
    context: list[str] = []
    section_title: str | None = None
    chapter_title: str | None = None
    for prev in art.find_all_previous(class_=re.compile(r"^oj-ti-section-1$")):
        label = normalize_text(prev.get_text(" ", strip=True))
        if label.upper().startswith("SECTION") and chapter_title is None and section_title is None:
            section_title = _with_subtitle(prev, label)
        elif label.upper().startswith("CHAPTER"):
            chapter_title = _with_subtitle(prev, label)
            break
    if chapter_title:
        context.append(chapter_title)
    if section_title:
        context.append(section_title)
    return tuple(context)


def _with_subtitle(heading: Tag, label: str) -> str:
    """Join "CHAPTER III" with its descriptive oj-ti-section-2 sibling, if any."""
    nxt = heading.find_next_sibling()
    if isinstance(nxt, Tag) and "oj-ti-section-2" in (nxt.get("class") or []):
        subtitle = normalize_text(nxt.get_text(" ", strip=True))
        if subtitle:
            return f"{label} — {subtitle}"
    return label


def _load_articles_and_recitals(soup: BeautifulSoup) -> list[Section]:
    sections: list[Section] = []
    for div in soup.find_all("div", class_="eli-subdivision"):
        div_id = div.get("id") or ""
        if isinstance(div_id, list):
            div_id = div_id[0]
        if div_id.startswith("rct_"):
            number = div_id.removeprefix("rct_")
            text = _block_text(div)
            if text:
                sections.append(
                    Section(
                        heading_path=(DOC_LABEL, "Preamble", f"Recital ({number})"),
                        text=text,
                    )
                )
        elif div_id.startswith("art_"):
            ti = div.find("p", class_="oj-ti-art")
            sti = div.find("p", class_="oj-sti-art")
            article_label = normalize_text(ti.get_text(" ", strip=True)) if ti else div_id
            if sti:
                article_label += " — " + normalize_text(sti.get_text(" ", strip=True))
            for title_p in (ti, sti):
                if isinstance(title_p, Tag):
                    title_p.extract()
            text = _block_text(div)
            if text:
                sections.append(
                    Section(
                        heading_path=(DOC_LABEL, *_article_context(div), article_label),
                        text=text,
                    )
                )
    return sections


def _load_annexes(soup: BeautifulSoup) -> list[Section]:
    """Annexes carry no eli ids; segment by consecutive oj-doc-ti headings."""
    boundaries: list[Tag] = [
        t
        for t in soup.find_all("p", class_="oj-doc-ti")
        if _ANNEX_RE.match(normalize_text(t.get_text(" ", strip=True)))
    ]
    sections: list[Section] = []
    for i, boundary in enumerate(boundaries):
        label = normalize_text(boundary.get_text(" ", strip=True))
        stop = boundaries[i + 1] if i + 1 < len(boundaries) else None
        title: str | None = None
        lines: list[str] = []
        for node in boundary.next_elements:
            if stop is not None and node is stop:
                break
            if not isinstance(node, NavigableString):
                continue
            parent = node.parent
            if not isinstance(parent, Tag):
                continue
            if parent.name in ("script", "style"):
                continue
            # The annex's descriptive title is the next oj-doc-ti after "ANNEX N".
            if "oj-doc-ti" in (parent.get("class") or []):
                if title is None:
                    title = normalize_text(str(node))
                continue
            # Stop at the end-of-document footnote panel.
            if parent.get("id") == "fnp_1" or any(
                anc.get("id") == "fnp_1" for anc in parent.parents
            ):
                break
            text = normalize_text(str(node))
            if text:
                lines.append(text)
        body = normalize_lines("\n".join(lines))
        if body:
            heading = f"{label.title()} — {title}" if title else label.title()
            sections.append(Section(heading_path=(DOC_LABEL, heading), text=body))
    return sections


def load_eurlex_html(path: Path) -> ParsedDocument:
    raw_bytes = path.read_bytes()
    soup = BeautifulSoup(raw_bytes.decode("utf-8"), "lxml")

    sections = _load_articles_and_recitals(soup)

    # Order: recitals precede articles in the document; find_all returned
    # document order already. Annexes follow the articles.
    sections.extend(_load_annexes(soup))

    return ParsedDocument(
        source_uri=SOURCE_URI,
        title=TITLE,
        raw_bytes=raw_bytes,
        sections=tuple(sections),
    )
