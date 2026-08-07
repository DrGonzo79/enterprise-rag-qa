"""SCREENING — gate fallback-sourced candidates in fusion, re-score the 120.

**This is not a result and cannot become one.** The confirmatory set is
unblinded and permanently closed (SPEC-007 KD-12 amendment 8), so any change
measured against it is designed with knowledge of the cases it is scored on.
The artifact this writes is typed `screening` and carries
`renderable_as_figure: false`; the report layer must refuse it as a figure.

**What the gate does.** When the full-text conjunction returns nothing,
`fulltext_search` currently falls back to an OR of the query's lexemes and those
candidates enter RRF weighted identically to the dense arm's best hit — a
full-text rank-1 scores 1/61, and so does a vector rank-1 (SPEC-004 amendment
6). The gate drops fallback-sourced candidates before fusion, so a query whose
conjunction is unsatisfiable gets the dense arm alone.

**The one number this exists to produce:** of the 20 questions where vector-only
found the gold and hybrid did not, how many does the gate recover.

Usage:
    uv run python -m scripts.screen_fallback_gate
"""

import asyncio
import collections
import json
import os
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.env import load_env
from rag_qa.ingest.embedder import OpenAIEmbeddingClient
from rag_qa.retrieval import search as search_module
from rag_qa.retrieval import service as service_module
from rag_qa.retrieval.search import CandidateRow, vector_search
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import RetrievedChunk
from scripts.query_plan import EVAL_SERVER_SETTINGS
from scripts.section_match import matches_section

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / "evals" / "retrieval_confirmatory.jsonl"
RESULT = REPO_ROOT / "evals" / "confirmatory-result.json"
OUT = REPO_ROOT / "evals" / "screen-fallback-gate.json"
K = 8

load_env()
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)

_UNGATED = search_module.fulltext_search


async def _gated_fulltext(
    session: Any, query: str, filters: Any = None, pool: int = search_module.CANDIDATE_POOL
) -> list[CandidateRow]:
    """The gate: fallback-sourced candidates never reach fusion.

    Implemented by filtering `via_fallback` rather than by disabling the
    fallback, so the branch still runs and the provenance field still records
    what it would have contributed. That keeps the screen a question about
    *fusion weighting* rather than about whether the fallback should exist.
    """
    rows = await _UNGATED(session, query, filters, pool)
    return [row for row in rows if not row.via_fallback]


def _hit(rank: int | None) -> bool:
    return rank is not None and rank <= K


def _rank_of(chunks: list[RetrievedChunk], prefix: str) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if matches_section(prefix, chunk.section_path):
            return rank
    return None


async def score(cases: list[dict[str, Any]], factory: Any, embedder: Any) -> dict[str, Any]:
    retriever = Retriever(factory, embedder)
    out: dict[str, Any] = {}
    for case in cases:
        hits = await retriever.retrieve(case["question"], k=K)
        query_vector = (await embedder.embed([case["question"]]))[0]
        async with factory() as session:
            dense = await vector_search(session, query_vector)
        vector_only = [
            RetrievedChunk(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_title=row.document_title,
                source_uri=row.source_uri,
                doc_type=row.doc_type,
                section_path=row.section_path,
                ordinal=row.ordinal,
                text=row.text,
                score=0.0,
                vector_rank=rank,
                fulltext_rank=None,
            )
            for rank, row in enumerate(dense[:K], start=1)
        ]
        out[case["id"]] = {
            "hybrid_rank": _rank_of(hits, case["expected_section_prefix"]),
            "vector_rank": _rank_of(vector_only, case["expected_section_prefix"]),
            "top_chunk_ids": [str(h.chunk_id) for h in hits],
            "top_sections": [h.section_path for h in hits[:3]],
        }
    return out


def attractors(scored: dict[str, Any], ids: list[str], lengths: dict[str, int]) -> dict[str, Any]:
    """Which chunks occupy hybrid's top slots, and how long they are.

    Reported at rank 1 and across the top 3 because the two answer different
    questions: rank 1 is what displaced the gold, and the top 3 is how
    concentrated the displacement is.
    """
    corpus_median = statistics.median(lengths.values())
    top1 = [scored[i]["top_chunk_ids"][0] for i in ids if scored[i]["top_chunk_ids"]]
    top3 = [c for i in ids for c in scored[i]["top_chunk_ids"][:3]]
    top8 = [c for i in ids for c in scored[i]["top_chunk_ids"]]
    c1, c3 = collections.Counter(top1), collections.Counter(top3)
    occupant_lengths = sorted(lengths[c] for c in c1)
    sorted_all = sorted(lengths.values())
    med_occ = statistics.median(occupant_lengths)
    percentile = sum(1 for x in sorted_all if x < med_occ) / len(sorted_all)
    return {
        "questions": len(ids),
        "distinct_chunks_at_rank_1": len(c1),
        "distinct_chunks_in_top_3": len(c3),
        "distinct_chunks_in_top_8": len(set(top8)),
        "most_frequent_at_rank_1": [
            {"chunk": c, "times": n, "length": lengths[c]} for c, n in c1.most_common(5)
        ],
        "most_frequent_in_top_3": [
            {"chunk": c, "times": n, "length": lengths[c]} for c, n in c3.most_common(5)
        ],
        "corpus_median_length": corpus_median,
        "rank_1_occupant_median_length": med_occ,
        "rank_1_occupant_length_ratio": round(med_occ / corpus_median, 2),
        "rank_1_occupant_length_percentile": round(percentile * 100),
    }


async def run() -> int:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    baseline = json.loads(RESULT.read_text(encoding="utf-8"))
    vector_wins = [
        row["id"]
        for row in baseline["per_case_primary"]
        if _hit(row["vector_rank"]) and not _hit(row["hybrid_rank"])
    ]
    hybrid_wins = [
        row["id"]
        for row in baseline["per_case_primary"]
        if _hit(row["hybrid_rank"]) and not _hit(row["vector_rank"])
    ]

    engine = create_async_engine(
        CORPUS_URL,
        pool_size=4,
        max_overflow=2,
        connect_args={"server_settings": EVAL_SERVER_SETTINGS},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(text("SELECT id, length(text) FROM chunks"))).all()
    lengths = {str(r[0]): int(r[1]) for r in rows}

    embedder = OpenAIEmbeddingClient()

    ungated = await score(cases, factory, embedder)

    # `service.py` binds `fulltext_search` at import, so patching the search
    # module alone would leave the gate inert and the screen would report the
    # baseline twice. Patch the bound name, then PROVE the patch took: a query
    # whose conjunction is unsatisfiable must return zero candidates under the
    # gate and more than zero without it.
    search_module.fulltext_search = _gated_fulltext  # type: ignore[assignment]
    service_module.fulltext_search = _gated_fulltext  # type: ignore[assignment]
    try:
        probe = (
            "What extra obligations does Article 55 place on providers of "
            "general-purpose AI models with systemic risk?"
        )
        async with factory() as session:
            gated_rows = await service_module.fulltext_search(session, probe, None)
            ungated_rows = await _UNGATED(session, probe, None)
        if gated_rows or not ungated_rows:
            print(
                f"gate did not take effect: gated={len(gated_rows)} ungated={len(ungated_rows)}",
                file=sys.stderr,
            )
            await engine.dispose()
            return 4
        print(f"  gate verified: probe returns {len(ungated_rows)} ungated, 0 gated")
        gated = await score(cases, factory, embedder)
    finally:
        search_module.fulltext_search = _UNGATED  # type: ignore[assignment]
        service_module.fulltext_search = _UNGATED  # type: ignore[assignment]
    await engine.dispose()

    recovered = [i for i in vector_wins if _hit(gated[i]["hybrid_rank"])]
    lost = [
        i
        for i in hybrid_wins
        if _hit(ungated[i]["hybrid_rank"]) and not _hit(gated[i]["hybrid_rank"])
    ]
    newly_broken = [
        c["id"]
        for c in cases
        if _hit(ungated[c["id"]]["hybrid_rank"]) and not _hit(gated[c["id"]]["hybrid_rank"])
    ]

    def recall(scored: dict[str, Any]) -> float:
        return round(sum(1 for v in scored.values() if _hit(v["hybrid_rank"])) / len(scored), 4)

    print("\n=== SCREENING — NOT A RESULT, NOT RENDERABLE AS A FIGURE ===\n")
    print(f"  vector-only wins recovered by the gate   {len(recovered)} of {len(vector_wins)}")
    print(f"  hybrid wins the gate gives up            {len(lost)} of {len(hybrid_wins)}")
    print(f"  questions hybrid answered and now misses {len(newly_broken)}")
    print(f"  hybrid recall@8   ungated {recall(ungated)}  ->  gated {recall(gated)}")
    vector_recall = baseline["primary"]["recall_at_8_vector_only"]
    print(f"  vector-only recall@8 (unchanged)         {vector_recall}")

    attr = attractors(ungated, vector_wins, lengths)
    print("\n  displacement concentration, across the 20 vector-only wins:")
    print(f"    distinct chunks at rank 1     {attr['distinct_chunks_at_rank_1']} of 20 slots")
    print(f"    distinct chunks in the top 3  {attr['distinct_chunks_in_top_3']} of 60 slots")
    print(
        f"    rank-1 occupant median length {attr['rank_1_occupant_median_length']} "
        f"({attr['rank_1_occupant_length_ratio']}x corpus median, "
        f"{attr['rank_1_occupant_length_percentile']}th percentile)"
    )
    for entry in attr["most_frequent_in_top_3"]:
        print(f"      x{entry['times']:<3} len={entry['length']:<6} {entry['chunk']}")

    payload = {
        "artifact_type": "screening",
        "renderable_as_figure": False,
        "why_not_a_figure": (
            "The confirmatory set is unblinded and permanently closed (KD-12 amendment 8). "
            "This gate was designed with knowledge of the cases it is scored on, so the "
            "numbers below are a screen for whether a fusion fix is worth pursuing, not "
            "evidence about whether it works. No p-value is computed and none may be."
        ),
        "measured_at": datetime.now(UTC).date().isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip(),
        "gate": "fallback-sourced candidates (via_fallback) dropped before RRF fusion",
        "n": len(cases),
        "vector_only_wins_in_baseline": len(vector_wins),
        "recovered_by_gate": recovered,
        "recovered_count": len(recovered),
        "hybrid_wins_given_up": lost,
        "questions_newly_broken": newly_broken,
        "hybrid_recall_at_8_ungated": recall(ungated),
        "hybrid_recall_at_8_gated": recall(gated),
        "attractors": attr,
        "per_case": {
            cid: {
                "ungated_hybrid_rank": ungated[cid]["hybrid_rank"],
                "gated_hybrid_rank": gated[cid]["hybrid_rank"],
                "vector_rank": ungated[cid]["vector_rank"],
            }
            for cid in sorted(ungated)
        },
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
