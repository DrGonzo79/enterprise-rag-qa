"""Retriever orchestration (SPEC-004): concurrent branch searches, embedder
identity verification, RRF fusion, reranker seam, per-query instrumentation.

Connection math (SPEC-004 KD-5, against SPEC-002 KD-8's pool bound of 10):
two sessions per in-flight retrieve — the identity check rides sequentially
on the full-text branch's session, so no third connection is taken.
"""

import asyncio
import hashlib
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.ingest.embedder import EmbeddingClient
from rag_qa.retrieval.fusion import rrf_fuse
from rag_qa.retrieval.metrics import distinct_section_rate
from rag_qa.retrieval.rerank import NoopReranker, Reranker
from rag_qa.retrieval.search import (
    CandidateRow,
    fetch_embedder_identities,
    fulltext_search,
    vector_search,
)
from rag_qa.retrieval.types import (
    EmbedderMismatchError,
    EmptyCorpusError,
    RetrievalFilters,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)

RERANK_WINDOW_FACTOR = 4


def _elapsed_ms(since: float) -> float:
    return (time.perf_counter() - since) * 1000.0


class Retriever:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        query_embedder: EmbeddingClient,
        reranker: Reranker | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._query_embedder = query_embedder
        self._reranker: Reranker = reranker if reranker is not None else NoopReranker()

    async def retrieve(
        self,
        query: str,
        k: int = 8,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query is empty or whitespace-only")

        started = time.perf_counter()
        vectors = await self._query_embedder.embed([query])
        query_vector = vectors[0]
        embed_ms = _elapsed_ms(started)

        async def vector_branch() -> tuple[list[CandidateRow], float]:
            branch_started = time.perf_counter()
            async with self._session_factory() as session:
                rows = await vector_search(session, query_vector, filters)
            return rows, _elapsed_ms(branch_started)

        async def fulltext_branch() -> tuple[set[str], list[CandidateRow], float]:
            # Identity check first, same session, sequentially — verified on
            # every call because ingestion can rewrite the corpus while the
            # service runs (SPEC-004 KD-4). Riding this branch keeps it off
            # the critical path: the vector branch runs meanwhile.
            branch_started = time.perf_counter()
            async with self._session_factory() as session:
                identities = await fetch_embedder_identities(session)
                rows = await fulltext_search(session, query, filters)
            return identities, rows, _elapsed_ms(branch_started)

        (vector_rows, vector_ms), (identities, fulltext_rows, fts_ms) = await asyncio.gather(
            vector_branch(), fulltext_branch()
        )

        if not identities:
            raise EmptyCorpusError("chunks table is empty; ingest a corpus first")
        if identities != {self._query_embedder.identity}:
            raise EmbedderMismatchError(
                f"stored corpus embedder(s) {sorted(identities)} do not match query "
                f"embedder '{self._query_embedder.identity}'; refusing to search with "
                "incompatible vectors (SPEC-004 KD-4)"
            )

        fuse_started = time.perf_counter()
        fused = rrf_fuse(vector_rows, fulltext_rows)
        fuse_ms = _elapsed_ms(fuse_started)

        results = await self._reranker.rerank(query, fused[: RERANK_WINDOW_FACTOR * k], k)

        logger.info(
            "retrieve",
            extra={
                "query_sha": hashlib.sha256(query.encode("utf-8")).hexdigest()[:12],
                "k": k,
                "result_count": len(results),
                "distinct_section_rate": distinct_section_rate(results),
                "embed_ms": round(embed_ms, 2),
                "vector_ms": round(vector_ms, 2),
                "fts_ms": round(fts_ms, 2),
                "fuse_ms": round(fuse_ms, 2),
                "total_ms": round(_elapsed_ms(started), 2),
            },
        )
        return results
