"""Result-diversity metric (SPEC-003 Key decision 12). Pure — no I/O.

The Retriever logs this per query and SPEC-007 imports the same function for
eval aggregation, so the production number and the eval number can never
diverge by construction."""

from collections.abc import Sequence

from rag_qa.retrieval.types import RetrievedChunk


def distinct_section_rate(chunks: Sequence[RetrievedChunk]) -> float:
    """len(unique section_path) / len(chunks); 0.0 for empty input."""
    if not chunks:
        return 0.0
    return len({c.section_path for c in chunks}) / len(chunks)
