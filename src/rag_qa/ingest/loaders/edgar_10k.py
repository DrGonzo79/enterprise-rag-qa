"""EDGAR 10-K (inline XBRL) loader, pinned to the NVDA FY2026 filing.

Measured (SPEC-003 Purpose): zero h1-h6 — headings are font-weight:700 spans;
"Item N." appears twice (TOC + body); 1,402 inline-XBRL tags; hidden ix:header
with ~19K chars of metadata; Windows-1252 bytes despite an ASCII claim; 64
tables holding ~8% of text. The >=50% digit-cell drop rule applies to THIS
loader only (Key decision 11).
"""

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from rag_qa.ingest.normalize import normalize_lines, normalize_text
from rag_qa.ingest.types import DroppedTable, IngestConfig, ParsedDocument, Section

logger = logging.getLogger(__name__)

SOURCE_URI = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"
TITLE = "NVIDIA Corporation Form 10-K (fiscal year 2026)"
DOC_LABEL = "NVIDIA 10-K FY2026"

_ITEM_RE = re.compile(r"^Item\s+(\d+[A-C]?)\.", re.IGNORECASE)
_NUMERIC_CELL_RE = re.compile(r"^[\d,.\s$%()—–-]*\d[\d,.\s$%()—–-]*$")
_BOLD_RE = re.compile(r"font-weight\s*:\s*(700|bold)")
_BLOCK_NAMES = ("div", "p", "td", "li", "h1", "h2", "h3", "h4", "h5", "h6")


def decode_edgar(raw_bytes: bytes) -> str:
    """UTF-8 first, cp1252 fallback; never errors='replace' (Key decision 9)."""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252")


def table_digit_ratio(table: Tag) -> tuple[float, int]:
    """Fraction of non-empty cells that are numeric, and the non-empty count."""
    cells = [normalize_text(td.get_text(" ", strip=True)) for td in table.find_all(["td", "th"])]
    cells = [c for c in cells if c]
    if not cells:
        return 0.0, 0
    numeric = sum(1 for c in cells if _NUMERIC_CELL_RE.match(c))
    return numeric / len(cells), len(cells)


def _linearize_table(table: Tag) -> str:
    rows: list[str] = []
    for tr in table.find_all("tr"):
        cells = [normalize_text(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _item_key(text: str) -> str | None:
    m = _ITEM_RE.match(text)
    return m.group(1).upper() if m else None


def _is_block(tag: Tag) -> bool:
    return tag.name in _BLOCK_NAMES


def _find_item_headings(soup: BeautifulSoup) -> dict[str, Tag]:
    """Body heading block per item key.

    Candidates are block elements whose text starts with "Item N." and stays
    short enough to be a heading. Document order means later overwrites
    earlier, which resolves both ambiguities at once: the body heading beats
    the TOC entry (verified: each Item appears twice, TOC first), and an
    inner block beats the outer one wrapping it (children follow parents).
    """
    headings: dict[str, Tag] = {}
    for tag in soup.find_all(_is_block):
        text = normalize_text(tag.get_text(" ", strip=True))
        if len(text) > 120:
            continue
        key = _item_key(text)
        if key is not None:
            headings[key] = tag
    return headings


def _is_subheading_block(tag: Tag, text: str) -> bool:
    """A block whose entire text is one bold span of heading-plausible length."""
    if not 5 <= len(text) <= 90:
        return False
    return any(
        _BOLD_RE.search(str(span.get("style") or ""))
        and normalize_text(span.get_text(" ", strip=True)) == text
        for span in tag.find_all("span")
    )


def _is_leaf_block(tag: Tag) -> bool:
    return _is_block(tag) and tag.find(_is_block) is None


def load_edgar_10k(path: Path, config: IngestConfig | None = None) -> ParsedDocument:
    config = config or IngestConfig()
    raw_bytes = path.read_bytes()
    soup = BeautifulSoup(decode_edgar(raw_bytes), "lxml")

    for stripped in soup.find_all(["ix:header", "script", "style"]):
        stripped.decompose()

    headings = _find_item_headings(soup)
    heading_ids = {id(tag): key for key, tag in headings.items()}

    # Document-order walk. State machine: current item -> current bold
    # subheading -> accumulated paragraph lines (one line per leaf block, so
    # sentences split across inline spans stay joined).
    sections: list[Section] = []
    dropped: list[DroppedTable] = []
    table_index = 0

    current_item: str | None = None
    current_sub: str | None = None
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        body = normalize_lines("\n".join(lines))
        lines = []
        if body and current_item is not None:
            path_parts = (DOC_LABEL, current_item) + ((current_sub,) if current_sub else ())
            sections.append(Section(heading_path=path_parts, text=body))

    root = soup.body or soup
    skip_within: Tag | None = None
    for node in root.descendants:
        if skip_within is not None:
            if any(parent is skip_within for parent in node.parents):
                continue
            skip_within = None

        if isinstance(node, Tag):
            if id(node) in heading_ids:
                flush()
                current_item = normalize_text(node.get_text(" ", strip=True))
                current_sub = None
                skip_within = node
            elif node.name == "table":
                if current_item is not None:
                    ratio, cell_count = table_digit_ratio(node)
                    if ratio >= config.edgar_numeric_table_threshold:
                        dropped.append(
                            DroppedTable(
                                document=path.name,
                                item=current_item,
                                table_index=table_index,
                                digit_ratio=round(ratio, 4),
                                cell_count=cell_count,
                                reason="numeric_table_threshold",
                            )
                        )
                        logger.info(
                            "dropped numeric table %d in %r (digit ratio %.2f)",
                            table_index,
                            current_item,
                            ratio,
                        )
                    else:
                        linearized = _linearize_table(node)
                        if linearized:
                            lines.extend(linearized.split("\n"))
                table_index += 1
                skip_within = node
            elif current_item is not None and _is_leaf_block(node):
                text = normalize_text(node.get_text(" ", strip=True))
                if text and _is_subheading_block(node, text):
                    flush()
                    current_sub = text
                elif text:
                    lines.append(text)
                skip_within = node
        elif isinstance(node, NavigableString) and current_item is not None:
            # Fallback for text hanging directly under a container block.
            text = normalize_text(str(node))
            if text:
                lines.append(text)

    flush()

    return ParsedDocument(
        source_uri=SOURCE_URI,
        title=TITLE,
        raw_bytes=raw_bytes,
        sections=tuple(sections),
        dropped_tables=tuple(dropped),
    )
