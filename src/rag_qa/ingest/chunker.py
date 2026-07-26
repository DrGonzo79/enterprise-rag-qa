"""Heading-aware, sentence-safe chunking (SPEC-003 Interface).

Unit of packing: a "sentence unit" — a pysbd sentence within a line. Lines are
hard boundaries because loaders emit one line per layout-table point row
("(a) ...;"), and legal semicolon chains would otherwise register as one giant
sentence.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import pysbd  # type: ignore[import-untyped]
import tiktoken

from rag_qa.ingest.types import ChunkDraft, IngestConfig, ParsedDocument, Section

logger = logging.getLogger(__name__)

_ENCODING = tiktoken.get_encoding("cl100k_base")
_SEGMENTER = pysbd.Segmenter(language="en", clean=False)

BREADCRUMB_SEPARATOR = " › "


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def sentence_units(text: str) -> list[str]:
    """pysbd sentences within each line; lines are hard boundaries."""
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        segments: list[str] = _SEGMENTER.segment(line)  # type: ignore[assignment]
        units.extend(s.strip() for s in segments if s.strip())
    return units


@dataclass
class _Unit:
    text: str
    tokens: int
    section_index: int


@dataclass
class _Group:
    """Consecutive sections sharing a parent heading, packed together."""

    parent: tuple[str, ...]
    sections: list[Section]
    units: list[_Unit]


def breadcrumb_allowance(heading_path: tuple[str, ...]) -> int:
    """Token budget reserved for the breadcrumb prefixed into chunk text
    (+ margin for merged-section labels like "Recital (5) – Recital (9)")."""
    return count_tokens(BREADCRUMB_SEPARATOR.join(heading_path)) + 16


def _split_oversized(unit: _Unit, max_tokens: int) -> Iterator[_Unit]:
    """Escape hatch: hard-split a single sentence longer than the unit budget."""
    if unit.tokens <= max_tokens:
        yield unit
        return
    logger.warning(
        "hard-splitting a %d-token sentence at a token boundary (section %d)",
        unit.tokens,
        unit.section_index,
    )
    tokens = _ENCODING.encode(unit.text)
    for start in range(0, len(tokens), max_tokens):
        piece = tokens[start : start + max_tokens]
        yield _Unit(_ENCODING.decode(piece).strip(), len(piece), unit.section_index)


def _build_groups(doc: ParsedDocument, config: IngestConfig) -> list[_Group]:
    groups: list[_Group] = []
    for index, section in enumerate(doc.sections):
        parent = section.heading_path[:-1]
        unit_budget = config.target_max - breadcrumb_allowance(section.heading_path)
        units = [
            split
            for raw in sentence_units(section.text)
            for split in _split_oversized(_Unit(raw, count_tokens(raw), index), unit_budget)
        ]
        if not units:
            continue
        section_tokens = sum(u.tokens for u in units)
        if (
            groups
            and groups[-1].parent == parent
            and sum(u.tokens for u in groups[-1].units) < config.target_min
            and sum(u.tokens for u in groups[-1].units) + section_tokens <= config.target_max
        ):
            groups[-1].sections.append(section)
            groups[-1].units.extend(units)
        else:
            groups.append(_Group(parent=parent, sections=[section], units=units))
    return groups


def _section_path(section_indices: set[int], doc: ParsedDocument) -> str:
    """Breadcrumb for a chunk: full path for one section, otherwise the common
    ancestor path plus a "first – last" range label."""
    paths = [doc.sections[i].heading_path for i in sorted(section_indices)]
    if len(paths) == 1:
        return BREADCRUMB_SEPARATOR.join(paths[0])

    prefix: list[str] = []
    for elements in zip(*paths, strict=False):
        if all(e == elements[0] for e in elements):
            prefix.append(elements[0])
        else:
            break

    def label(path: tuple[str, ...]) -> str:
        return path[len(prefix)] if len(path) > len(prefix) else path[-1]

    leaf = f"{label(paths[0])} – {label(paths[-1])}"
    return BREADCRUMB_SEPARATOR.join((*prefix, leaf))


def _overlap_units(
    previous: list[_Unit], boundary_section: int, config: IngestConfig
) -> list[_Unit]:
    """Trailing whole sentences of the previous chunk, all from the section the
    new chunk starts in (no overlap across heading boundaries), totaling at
    least overlap_ratio * target_max tokens — first sentence crossing the
    threshold completes the overlap."""
    threshold = int(config.overlap_ratio * config.target_max)
    taken: list[_Unit] = []
    total = 0
    # Never take the whole previous chunk as overlap.
    for unit in reversed(previous[1:]):
        if unit.section_index != boundary_section:
            break
        taken.append(unit)
        total += unit.tokens
        if total >= threshold:
            break
    return list(reversed(taken))


def chunk_document(doc: ParsedDocument, config: IngestConfig) -> list[ChunkDraft]:
    all_packed: list[list[_Unit]] = []

    for group in _build_groups(doc, config):
        # The breadcrumb is prefixed into chunk text and counts against the
        # token budget: reserve the group's longest full path plus the longest
        # leaf again (range labels repeat a leaf: "Article 88 … – Article 91 …").
        longest_path = max(
            count_tokens(BREADCRUMB_SEPARATOR.join(s.heading_path)) for s in group.sections
        )
        longest_leaf = max(count_tokens(s.heading_path[-1]) for s in group.sections)
        budget = max(config.target_max - (longest_path + longest_leaf + 8), config.hard_min)

        packed: list[list[_Unit]] = []
        current: list[_Unit] = []
        current_tokens = 0

        for unit in group.units:
            if current and current_tokens + unit.tokens > budget:
                packed.append(current)
                overlap = _overlap_units(current, unit.section_index, config)
                while overlap and sum(u.tokens for u in overlap) + unit.tokens > budget:
                    overlap.pop(0)
                current = [*overlap, unit]
                current_tokens = sum(u.tokens for u in current)
            else:
                current.append(unit)
                current_tokens += unit.tokens
        if current:
            packed.append(current)

        # Tail redistribution: a final chunk under hard_min steals trailing
        # sentences from its predecessor until both clear the floor. Overlap
        # units are shared objects — stop before creating an adjacent
        # duplicate of the tail's seeded overlap.
        if len(packed) >= 2:
            tail, prev = packed[-1], packed[-2]
            while (
                sum(u.tokens for u in tail) < config.hard_min
                and len(prev) > 1
                and sum(u.tokens for u in prev[:-1]) >= config.hard_min
                and prev[-1] is not tail[0]
            ):
                tail.insert(0, prev.pop())

        all_packed.extend(packed)

    def rendered(units: list[_Unit]) -> ChunkDraft:
        path = _section_path({u.section_index for u in units}, doc)
        text = path + "\n" + " ".join(u.text for u in units)
        return ChunkDraft(text=text, token_count=count_tokens(text), section_path=path)

    # Rescue merge: a chunk under hard_min (an isolated section with no
    # same-parent sibling to pack with — "Item 1B. None.") joins an adjacent
    # chunk when the combined chunk stays within target_max. The breadcrumb
    # becomes the common-ancestor range ("Item 1A … – Item 1B …").
    i = 0
    while i < len(all_packed):
        if sum(u.tokens for u in all_packed[i]) >= config.hard_min:
            i += 1
            continue
        if i > 0 and rendered(all_packed[i - 1] + all_packed[i]).token_count <= config.target_max:
            all_packed[i - 1] = all_packed[i - 1] + all_packed[i]
            del all_packed[i]
            i -= 1
        elif (
            i + 1 < len(all_packed)
            and rendered(all_packed[i] + all_packed[i + 1]).token_count <= config.target_max
        ):
            all_packed[i] = all_packed[i] + all_packed[i + 1]
            del all_packed[i + 1]
        else:
            logger.warning(
                "chunk below hard_min could not merge with a neighbor (%d tokens)",
                sum(u.tokens for u in all_packed[i]),
            )
            i += 1

    chunks: list[ChunkDraft] = []
    for units in all_packed:
        draft = rendered(units)
        if draft.token_count > config.target_max:
            logger.warning(
                "chunk exceeds target_max after breadcrumb (%d tokens): %s",
                draft.token_count,
                draft.section_path,
            )
        chunks.append(draft)
    return chunks
