"""NIST AI RMF PDF loader: sectioning driven by the PDF outline (bookmarks).

Measured (SPEC-003 Purpose): 48 pages, outline 4 levels deep, running header
"NIST AI 100-1 AI RMF 1.0" on 43/48 pages, line-break hyphenation, front
matter (title page, TOC, lists of figures/tables) before "Executive Summary".
"""

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import Destination

from rag_qa.ingest.normalize import dehyphenate, normalize_text, strip_page_furniture
from rag_qa.ingest.types import ParsedDocument, Section

SOURCE_URI = "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf"
TITLE = "NIST AI Risk Management Framework (AI RMF 1.0)"
DOC_LABEL = "NIST AI RMF 1.0"
FIRST_BODY_SECTION = "Executive Summary"


@dataclass(frozen=True)
class _OutlineEntry:
    title: str
    depth: int
    page_index: int


def _flatten_outline(reader: PdfReader) -> list[_OutlineEntry]:
    entries: list[_OutlineEntry] = []

    def walk(nodes: object, depth: int) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(node, list):
                walk(node, depth + 1)  # pyright: ignore[reportUnknownArgumentType]
            elif isinstance(node, Destination):
                title = normalize_text(str(node.title or ""))
                page = reader.get_destination_page_number(node)
                if page is not None:
                    entries.append(_OutlineEntry(title, depth, page))

    walk(reader.outline, 0)
    return entries


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _match_window(lines: list[str], start: int, title: str, max_window: int = 3) -> int:
    """Number of consecutive lines at `start` that together form the heading
    (long titles wrap in the PDF, e.g. "Appendix A:" + its continuation);
    0 when they don't. Accepts a numbered form ("2.1 Title"). TOC entries
    never match: they carry a trailing page number."""
    title_c = _collapse(title)
    acc = ""
    for width in range(1, max_window + 1):
        if start + width > len(lines):
            break
        acc = _collapse(f"{acc} {lines[start + width - 1]}")
        if acc == title_c or (
            acc.endswith(title_c) and re.fullmatch(r"[\d.]+\s*", acc[: -len(title_c)]) is not None
        ):
            return width
        if len(acc) > len(title_c) + 8:
            break
    return 0


def load_nist_pdf(path: Path) -> ParsedDocument:
    raw_bytes = path.read_bytes()
    reader = PdfReader(BytesIO(raw_bytes))

    pages = [dehyphenate(page.extract_text() or "") for page in reader.pages]
    pages = strip_page_furniture(pages)

    # One flat list of lines with the starting line index of each page.
    lines: list[str] = []
    page_first_line: list[int] = []
    for page in pages:
        page_first_line.append(len(lines))
        lines.extend(page.split("\n"))

    entries = _flatten_outline(reader)
    body_start = next((i for i, e in enumerate(entries) if e.title == FIRST_BODY_SECTION), 0)
    entries = entries[body_start:]

    # Heading position per entry: first window of lines matching the title
    # at/after the entry's bookmarked page, never before the previous entry's
    # heading (some bookmarks carry wrong page numbers — verified). Falls back
    # to the ordered floor when the title isn't found at all.
    positions: list[tuple[int, int]] = []  # (line index, heading window width)
    floor = 0
    for entry in entries:
        page_line = page_first_line[min(entry.page_index, len(page_first_line) - 1)]
        start = max(page_line, floor)
        candidates = (
            (i, w) for i in range(start, len(lines)) if (w := _match_window(lines, i, entry.title))
        )
        found = next(candidates, (start, 0))
        positions.append(found)
        floor = found[0] + found[1]

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for i, entry in enumerate(entries):
        while stack and stack[-1][0] >= entry.depth:
            stack.pop()
        stack.append((entry.depth, entry.title))
        heading_path = (DOC_LABEL, *[title for _, title in stack])

        # Body: lines strictly after the heading window, up to the next
        # heading. When the heading wasn't found (width 0), keep the line.
        line_index, width = positions[i]
        start = line_index + width
        end = positions[i + 1][0] if i + 1 < len(entries) else len(lines)
        body = normalize_text(" ".join(lines[start:end]))
        if body:
            sections.append(Section(heading_path=heading_path, text=body))

    return ParsedDocument(
        source_uri=SOURCE_URI,
        title=TITLE,
        doc_type="standard",
        raw_bytes=raw_bytes,
        sections=tuple(sections),
    )
