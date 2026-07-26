"""Reciprocal Rank Fusion (SPEC-004 Key decisions 1–2). Pure — no I/O.

RRF consumes only ranks, so the incommensurable scales of cosine distance and
ts_rank_cd never meet. RRF_K = 60 is the Cormack/Clarke/Buettcher (2009)
default: large enough that rank-1-vs-2 differences don't dominate, small
enough that top ranks still matter. Tuning it is SPEC-007's job.
"""

import uuid

from rag_qa.retrieval.search import CandidateRow
from rag_qa.retrieval.types import RetrievedChunk

RRF_K = 60


def _best_rank(chunk: RetrievedChunk) -> int:
    ranks = [r for r in (chunk.vector_rank, chunk.fulltext_rank) if r is not None]
    return min(ranks)


def rrf_fuse(
    vector: list[CandidateRow],
    fulltext: list[CandidateRow],
    *,
    rrf_k: int = RRF_K,
) -> list[RetrievedChunk]:
    """Fuse the two ranked lists; returns ALL candidates, fused order.

    score(c) = Σ over lists containing c of 1 / (rrf_k + rank), ranks 1-based.
    Deterministic total order: score desc, then best single-list rank asc,
    then chunk_id asc. Either list may be empty (degrades to the other's
    order); both empty fuses to [].
    """
    vector_ranks = {row.chunk_id: i + 1 for i, row in enumerate(vector)}
    fulltext_ranks = {row.chunk_id: i + 1 for i, row in enumerate(fulltext)}
    rows: dict[uuid.UUID, CandidateRow] = {}
    for row in [*vector, *fulltext]:
        rows.setdefault(row.chunk_id, row)

    fused: list[RetrievedChunk] = []
    for chunk_id, row in rows.items():
        vector_rank = vector_ranks.get(chunk_id)
        fulltext_rank = fulltext_ranks.get(chunk_id)
        score = sum(1.0 / (rrf_k + r) for r in (vector_rank, fulltext_rank) if r is not None)
        fused.append(
            RetrievedChunk(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_title=row.document_title,
                source_uri=row.source_uri,
                doc_type=row.doc_type,
                section_path=row.section_path,
                ordinal=row.ordinal,
                text=row.text,
                score=score,
                vector_rank=vector_rank,
                fulltext_rank=fulltext_rank,
            )
        )

    fused.sort(key=lambda c: (-c.score, _best_rank(c), str(c.chunk_id)))
    return fused
