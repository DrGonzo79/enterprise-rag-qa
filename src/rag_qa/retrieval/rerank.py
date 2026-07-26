"""Reranker seam (SPEC-004 Key decision on the stub): position fixed now —
post-fusion, pre-truncation — so a real cross-encoder slots in without
touching any call site. The implementation is cuttable per the scope-cut
ladder; the seam is not."""

from typing import Protocol

from rag_qa.retrieval.types import RetrievedChunk


class Reranker(Protocol):
    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], k: int
    ) -> list[RetrievedChunk]: ...


class NoopReranker:
    """Returns the fused order truncated to k."""

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], k: int
    ) -> list[RetrievedChunk]:
        return candidates[:k]
