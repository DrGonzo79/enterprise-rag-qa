"""Retrieval result and filter types (SPEC-004 Interface)."""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_uri: str
    doc_type: str
    # breadcrumb like "EU AI Act › CHAPTER III › SECTION 1 › Article 6 — …"
    section_path: str
    ordinal: int
    text: str
    # fused RRF score; becomes the reranker's score once a real reranker exists
    score: float
    vector_rank: int | None
    fulltext_rank: int | None


@dataclass(frozen=True)
class RetrievalFilters:
    """Pushed into BOTH branch queries before ranking, never post-applied
    (SPEC-004 review amendment 2). Fields AND together; values OR together."""

    document_ids: tuple[uuid.UUID, ...] | None = None
    source_uris: tuple[str, ...] | None = None
    doc_types: tuple[str, ...] | None = None


class EmbedderMismatchError(RuntimeError):
    """Stored corpus vectors and the query embedder disagree (SPEC-004 KD-4)."""


class EmptyCorpusError(RuntimeError):
    """The chunks table is empty; there is nothing to retrieve from."""
