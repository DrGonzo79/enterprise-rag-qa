"""Retriever orchestration (SPEC-004): concurrent branch searches, embedder
identity verification, reranker seam, per-query instrumentation.

Connection math (SPEC-004 KD-5, against SPEC-002 KD-8's pool bound of 10):
two sessions per in-flight retrieve — the identity check rides sequentially
on the full-text branch's session, so no third connection is taken.
"""

import hashlib
import logging
import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_qa.ingest.embedder import EmbeddingClient
from rag_qa.retrieval.metrics import distinct_section_rate
from rag_qa.retrieval.rerank import NoopReranker, Reranker
from rag_qa.retrieval.search import (
    fetch_embedder_identities,
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

# The score a caller sees. Kept as 1/(k + rank) with the RRF constant rather
# than switched to raw cosine similarity, because SPEC-006's response contract
# and SPEC-009's ranking both read `score` and neither should change meaning as
# a side effect of deleting a branch. With one arm it is a monotone function of
# rank and nothing more -- which is exactly what it was before for any chunk
# only one arm returned.
RANK_SCORE_K = 60


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
        # A plain callable, not a metrics object: the embedding round-trip is the
        # dominant term in query latency and the one that moves the SPEC-006
        # KD-10 shed threshold (amendment 5), so the API layer needs to see it --
        # but retrieval importing `rag_qa.api` to say so would invert the
        # dependency for a single float. Set after construction because the app
        # is handed a retriever in some paths and builds one in others, and one
        # wiring point beats two constructors that must agree.
        self.on_embed_latency: Callable[[float], None] | None = None

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
        if self.on_embed_latency is not None:
            # Successful round-trips only, deliberately. A connection refused in
            # 5 ms is also an `embed` outcome, and counting it here would pull
            # p95 *down* -- masking the degradation this series exists to show.
            # Failures are `rag_qa_errors_total`, which is the right series for
            # them and a different question.
            self.on_embed_latency(embed_ms / 1000.0)

        # ONE session, and one connection (SPEC-004 KD-17). Two branches used to
        # run concurrently here, which is why `CONNECTIONS_PER_QUERY` was 2 and
        # why KD-10 bounded concurrency below the pool at all. The embedder
        # identity check rode the full-text session; with that branch gone it
        # rides this one, sequentially and before the search, so a mismatched
        # corpus is refused without paying for a search first.
        branch_started = time.perf_counter()
        async with self._session_factory() as session:
            identities = await fetch_embedder_identities(session)
            if not identities:
                raise EmptyCorpusError("chunks table is empty; ingest a corpus first")
            if identities != {self._query_embedder.identity}:
                raise EmbedderMismatchError(
                    f"stored corpus embedder(s) {sorted(identities)} do not match query "
                    f"embedder '{self._query_embedder.identity}'; refusing to search with "
                    "incompatible vectors (SPEC-004 KD-4)"
                )
            vector_rows = await vector_search(session, query_vector, filters)
        vector_ms = _elapsed_ms(branch_started)

        candidates = [
            RetrievedChunk(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_title=row.document_title,
                source_uri=row.source_uri,
                doc_type=row.doc_type,
                section_path=row.section_path,
                ordinal=row.ordinal,
                text=row.text,
                score=1.0 / (RANK_SCORE_K + rank),
                vector_rank=rank,
            )
            for rank, row in enumerate(vector_rows, start=1)
        ]

        results = await self._reranker.rerank(query, candidates[: RERANK_WINDOW_FACTOR * k], k)

        logger.info(
            "retrieve",
            extra={
                "query_sha": hashlib.sha256(query.encode("utf-8")).hexdigest()[:12],
                "k": k,
                "result_count": len(results),
                "distinct_section_rate": distinct_section_rate(results),
                "embed_ms": round(embed_ms, 2),
                "vector_ms": round(vector_ms, 2),
                "total_ms": round(_elapsed_ms(started), 2),
            },
        )
        return results
