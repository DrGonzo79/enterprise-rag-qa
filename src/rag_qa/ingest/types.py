"""Shared ingestion dataclasses (SPEC-003 Interface)."""

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class IngestConfig:
    """The sole source of chunk-affecting parameters; serialized into content_hash.

    Every parameter that can change chunk output must live here (SPEC-002 Key
    decision 9 / SPEC-003 review amendment 3).
    """

    strategy: str = "heading_v1"
    target_min: int = 500
    target_max: int = 800
    overlap_ratio: float = 0.15
    hard_min: int = 120
    edgar_numeric_table_threshold: float = 0.5
    breadcrumb_format: str = "v1:›"

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def compute_content_hash(raw_bytes: bytes, config: IngestConfig) -> str:
    """sha256 over raw content ‖ chunking config (SPEC-002 Key decision 9)."""
    return hashlib.sha256(raw_bytes + b"\x00" + config.canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DroppedTable:
    document: str
    item: str
    table_index: int
    digit_ratio: float
    cell_count: int
    reason: str


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    source_uri: str
    title: str
    # semantic category, set per loader (SPEC-004): standard | regulation | filing
    doc_type: str
    raw_bytes: bytes
    sections: tuple[Section, ...]
    dropped_tables: tuple[DroppedTable, ...] = field(default=())


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    token_count: int
    section_path: str
