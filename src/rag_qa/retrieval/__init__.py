"""Hybrid retrieval (SPEC-004): dense + full-text, RRF fusion, citation
metadata, embedder-identity verification."""

from rag_qa.retrieval.fusion import RRF_K, rrf_fuse
from rag_qa.retrieval.metrics import distinct_section_rate
from rag_qa.retrieval.rerank import NoopReranker, Reranker
from rag_qa.retrieval.search import CANDIDATE_POOL
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import (
    EmbedderMismatchError,
    EmptyCorpusError,
    RetrievalFilters,
    RetrievedChunk,
)

__all__ = [
    "CANDIDATE_POOL",
    "RRF_K",
    "EmbedderMismatchError",
    "EmptyCorpusError",
    "NoopReranker",
    "Reranker",
    "RetrievalFilters",
    "RetrievedChunk",
    "Retriever",
    "distinct_section_rate",
    "rrf_fuse",
]
