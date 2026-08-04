"""Pilot sizing study (SPEC-007 Key decision 12, amendment 2).

Measures the discordance rate `r` on `evals/retrieval_pilot.jsonl` against the
**unchanged 358-chunk corpus**, so that `N = 6 / r` becomes a calculation rather
than the assumption prereg-2 carries. Nothing is fetched and nothing is ingested.

**This is not a quality measurement and its numbers may not be published.** At
n = 14 a recall figure has an interval wide enough to contain almost anything.
The study reports `r` and a direction.

Usage:
    uv run python -m scripts.run_pilot
"""

import asyncio
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.env import load_env
from rag_qa.ingest.embedder import OpenAIEmbeddingClient
from rag_qa.retrieval.search import vector_search
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import RetrievedChunk
from scripts.mcnemar import (
    MIN_DISCORDANT_FOR_ANY_REJECTION,
    mcnemar_exact_two_sided,
    power,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PILOT_SET = REPO_ROOT / os.environ.get("RAG_QA_PILOT_SET", "evals/retrieval_pilot.jsonl")
SMOKE_SET = REPO_ROOT / "evals" / "retrieval_smoke.jsonl"
OUT = REPO_ROOT / os.environ.get("RAG_QA_PILOT_OUT", "evals/pilot-1.json")
K = 8
PILOT_ID = os.environ.get("RAG_QA_PILOT_ID", "pilot-1")

load_env()
# The pilot set is parameterised so pilot-2 reuses the identical measurement
# path: a second copy of this script would be a second chance to differ from it.
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)


def load_cases(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def rank_of(chunks: list[RetrievedChunk], prefix: str) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if chunk.section_path.startswith(prefix):
            return rank
    return None


async def measure(cases: list[dict[str, str]]) -> list[dict[str, object]]:
    engine = create_async_engine(CORPUS_URL, pool_size=4, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chunk_count = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    embedder = OpenAIEmbeddingClient()
    retriever = Retriever(factory, embedder)

    from rag_qa.retrieval.search import fulltext_search

    rows: list[dict[str, object]] = []
    for case in cases:
        prefix = case["expected_section_prefix"]
        # How many candidates the full-text branch produced at all. Recorded
        # because a branch that returns nothing cannot make hybrid differ from
        # vector-only, and the whole comparison then measures one arm twice.
        async with factory() as session:
            fts_rows = len(await fulltext_search(session, case["question"], None))
        hybrid = await retriever.retrieve(case["question"], k=K)
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
        rows.append(
            {
                "id": case["id"],
                "shape": case.get("shape", ""),
                "hybrid_rank": rank_of(hybrid, prefix),
                "vector_rank": rank_of(vector_only, prefix),
                "fulltext_candidates": fts_rows,
                "identical_top_k": [c.chunk_id for c in hybrid]
                == [c.chunk_id for c in vector_only],
                "top_section": hybrid[0].section_path if hybrid else None,
            }
        )
    await engine.dispose()
    rows.append({"_chunk_count": chunk_count})  # carried out for the artifact
    return rows


def summarise(rows: list[dict[str, object]], label: str) -> dict[str, object]:
    def ok(row: dict[str, object], key: str) -> bool:
        rank = row[key]
        return isinstance(rank, int) and rank <= K

    b = sum(1 for r in rows if ok(r, "hybrid_rank") and not ok(r, "vector_rank"))
    c = sum(1 for r in rows if ok(r, "vector_rank") and not ok(r, "hybrid_rank"))
    both = sum(1 for r in rows if ok(r, "hybrid_rank") and ok(r, "vector_rank"))
    neither = len(rows) - b - c - both
    n_discordant = b + c
    return {
        "set": label,
        "n": len(rows),
        "recall_at_8_hybrid": round(sum(1 for r in rows if ok(r, "hybrid_rank")) / len(rows), 4),
        "recall_at_8_vector_only": round(
            sum(1 for r in rows if ok(r, "vector_rank")) / len(rows), 4
        ),
        "hybrid_only": b,
        "vector_only": c,
        "both": both,
        "neither": neither,
        "n_discordant": n_discordant,
        "r": round(n_discordant / len(rows), 4),
        "p": round(mcnemar_exact_two_sided(b, c), 6),
        # The reason r can be 0 without the methods being equally good: if the
        # full-text branch returns nothing, hybrid IS vector-only and the
        # comparison is one arm measured twice.
        "questions_with_no_fulltext_candidates": sum(
            1 for r in rows if r.get("fulltext_candidates") == 0
        ),
        "questions_where_top_k_is_identical": sum(1 for r in rows if r.get("identical_top_k")),
    }


def git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.stdout.strip() or "unknown"


async def run() -> int:
    pilot_cases = load_cases(PILOT_SET)
    smoke_cases = load_cases(SMOKE_SET)

    overlap = {c["id"] for c in pilot_cases} & {c["id"] for c in smoke_cases}
    if overlap:
        print(f"pilot shares ids with the smoke set: {sorted(overlap)}", file=sys.stderr)
        return 2

    pilot_rows = await measure(pilot_cases)
    chunk_count = pilot_rows.pop()["_chunk_count"]
    smoke_rows = await measure(smoke_cases)
    smoke_rows.pop()

    pilot = summarise(pilot_rows, "pilot (hard, authored without an expected section)")
    smoke = summarise(smoke_rows, "smoke (existing 26, article number in the question)")

    r = float(pilot["r"])  # type: ignore[arg-type]
    pooled_n = int(pilot["n"]) + int(smoke["n"])  # type: ignore[arg-type]
    pooled_discordant = int(pilot["n_discordant"]) + int(smoke["n_discordant"])  # type: ignore[arg-type]

    def upper_95(observed: int, trials: int) -> float:
        """One-sided 95% upper bound on a rate. With zero observations the point
        estimate is 0 and `N = 6/r` is undefined, so the bound is the only honest
        thing to report: it says how small r could still be and still have
        produced no discordance in `trials` questions."""
        if observed:
            return observed / trials
        return 1 - 0.05 ** (1 / trials)

    r_upper = upper_95(int(pilot["n_discordant"]), int(pilot["n"]))  # type: ignore[arg-type]
    pooled_upper = upper_95(pooled_discordant, pooled_n)
    n_required = math.ceil(MIN_DISCORDANT_FOR_ANY_REJECTION / r) if r > 0 else None
    n_lower_bound = math.ceil(MIN_DISCORDANT_FOR_ANY_REJECTION / r_upper)
    n_lower_bound_pooled = math.ceil(MIN_DISCORDANT_FOR_ANY_REJECTION / pooled_upper)

    print(f"\ncorpus: {chunk_count} chunks (unchanged)\n")
    print(
        f"{'':<12}{'n':>4}{'rec@8 h':>9}{'rec@8 v':>9}{'b':>4}{'c':>4}"
        f"{'both':>6}{'neither':>9}{'r':>7}"
    )
    for s in (smoke, pilot):
        print(
            f"{s['set'][:11]:<12}{s['n']:>4}{s['recall_at_8_hybrid']:>9}"
            f"{s['recall_at_8_vector_only']:>9}{s['hybrid_only']:>4}{s['vector_only']:>4}"
            f"{s['both']:>6}{s['neither']:>9}{s['r']:>7}"
        )

    print("\nper-case (pilot):")
    for row in pilot_rows:
        print(
            f"  {row['id']:<8}{str(row['shape'])[:22]:<24}"
            f"hybrid {row['hybrid_rank']!s:>5}  vector {row['vector_rank']!s:>5}"
        )

    power_table = {
        str(theta): round(power(MIN_DISCORDANT_FOR_ANY_REJECTION, theta), 3)
        for theta in (0.7, 0.75, 0.8, 0.9)
    }
    print("\nsizing:")
    if n_required:
        print(f"  N = {MIN_DISCORDANT_FOR_ANY_REJECTION} / r = {r} -> N = {n_required} questions")
    else:
        print(f"  r = 0 over {pilot['n']} questions: N = 6/r is UNDEFINED, not large.")
        print(f"  95% upper bound on r from the pilot alone: {r_upper:.4f} -> N >= {n_lower_bound}")
        print(
            f"  pooled with the smoke set ({pooled_discordant} of {pooled_n}): "
            f"r <= {pooled_upper:.4f} -> N >= {n_lower_bound_pooled}"
        )
        print("  Both are LOWER bounds. There is no upper bound and no finite N.")
    print(f"  power at the floor (n = {MIN_DISCORDANT_FOR_ANY_REJECTION}): {power_table}")

    payload = {
        "pilot_id": PILOT_ID,
        "measured_at": datetime.now(UTC).date().isoformat(),
        "git_sha": git_sha(),
        "corpus_chunks": chunk_count,
        "k": K,
        "excluded_from_confirmatory_set": True,
        "case_ids": [c["id"] for c in pilot_cases],
        "labels_human_verified": False,
        "pilot": pilot,
        "smoke_comparison": smoke,
        "sizing": {
            "min_discordant_for_any_rejection": MIN_DISCORDANT_FOR_ANY_REJECTION,
            "r_measured": r,
            "r_upper_95_pilot": round(r_upper, 4),
            "r_upper_95_pooled": round(pooled_upper, 4),
            "pooled_n": pooled_n,
            "pooled_discordant": pooled_discordant,
            "N_required": n_required,
            "N_lower_bound_pilot": n_lower_bound,
            "N_lower_bound_pooled": n_lower_bound_pooled,
            "power_at_floor": power_table,
            "note": (
                "With r = 0 the sizing calculation N = 6/r is undefined rather than "
                "large. The bounds are one-sided 95% upper bounds on r and therefore "
                "LOWER bounds on N; the pilot places no upper bound on N at all. "
                "Pooling with the smoke set mixes a deliberately hard set with a "
                "deliberately easy one and is reported as a sanity check, not as a "
                "rate for either population."
            ),
        },
        "per_case": pilot_rows,
        "not_a_claim": (
            "No figure here is a quality result. n=14 sizes an instrument; it does "
            "not measure retrieval."
        ),
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {OUT.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
