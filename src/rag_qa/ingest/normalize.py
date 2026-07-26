"""Shared text normalization applied by every loader (SPEC-003 Interface)."""

import re
import unicodedata
from collections import Counter

_WS_RUN = re.compile(r"\s+")
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-\n(\w)")
_PAGE_NUMBER_LINE = re.compile(r"^\d{1,3}$")


def dehyphenate(text: str) -> str:
    """Join words split by line-break hyphenation ("bene-\\nfits" -> "benefits")."""
    return _LINE_BREAK_HYPHEN.sub(r"\1\2", text)


def normalize_text(text: str) -> str:
    """NFKC, NBSP -> space, collapse whitespace runs."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ")
    return _WS_RUN.sub(" ", text).strip()


def normalize_lines(text: str) -> str:
    """Like normalize_text but preserves line boundaries (chunker unit boundaries)."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def strip_page_furniture(pages: list[str]) -> list[str]:
    """Remove repeated running headers/footers and bare page-number lines.

    A line is furniture when it equals the modal first or last line across
    pages and that modal value appears on more than half of the pages.
    """
    firsts: Counter[str] = Counter()
    lasts: Counter[str] = Counter()
    split_pages: list[list[str]] = []
    for page in pages:
        lines = [line.strip() for line in page.split("\n")]
        lines = [line for line in lines if line]
        split_pages.append(lines)
        if lines:
            firsts[lines[0]] += 1
            lasts[lines[-1]] += 1

    furniture: set[str] = set()
    for counter in (firsts, lasts):
        if counter:
            value, count = counter.most_common(1)[0]
            if count > len(pages) / 2:
                furniture.add(value)

    cleaned: list[str] = []
    for lines in split_pages:
        kept = [
            line for line in lines if line not in furniture and not _PAGE_NUMBER_LINE.match(line)
        ]
        cleaned.append("\n".join(kept))
    return cleaned
