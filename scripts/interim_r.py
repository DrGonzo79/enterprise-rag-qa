"""The blinded interim estimate of `r` (SPEC-007 Key decision 12 amendment 5, AC-17).

Measures the discordance **rate** on the first block of the confirmatory set so
the owner can choose N having spent 1.25 hours of authoring rather than 6.

**Why an interim look at `r` is safe, and why it is safe only under blinding.**
McNemar's exact test *conditions on* `n = b + c`; conditional on `n`, the
statistic is `b`, and under H0 `b ~ Binomial(n, 1/2)`. `r` is a rate of `n` —
a nuisance parameter — so a sizing rule that reads only the discordance
*indicators* leaves the null distribution of the statistic untouched and the
test's size preserved. **The moment `b` and `c` are inspected separately the
argument is void**, because the continuation decision becomes a function of the
statistic. Seeing 8-1 at question 30 and choosing to continue is a peek at the
effect no matter what reason is given for continuing.

**So the split is never computed here.** Not computed and suppressed — never
computed. `case_record` is the blinding boundary: direction exists on the way
in and does not exist on the way out, the artifact carries no per-case ranks,
and the full run re-measures from scratch rather than reading anything back.

**Bound:** this is a property of the reporting path, not a cryptographic seal.
The questions, the corpus and this code are all public, so anyone can
deliberately re-measure the split in about a minute. What is foreclosed is
seeing it *as a side effect of asking for the interim number*.

Usage:
    uv run python -m scripts.interim_r
"""

import asyncio
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.env import load_env
from rag_qa.ingest.embedder import OpenAIEmbeddingClient
from rag_qa.retrieval.search import vector_search
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import RetrievedChunk
from scripts.mcnemar import (
    MIN_DISCORDANT_FOR_ANY_REJECTION,
    clopper_pearson,
    min_discordant_for_power,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIRMATORY_SET = REPO_ROOT / "evals" / "retrieval_confirmatory.jsonl"
K = 8
THETA = 0.8

# Pre-committed in SPEC-007 KD-12 amendment 5. 15% of 30 is 4.5, so the two
# minority shapes alternate across blocks and land exactly on 105/23/22 at
# N = 150. The interim REFUSES TO RUN on a block that is not its committed mix:
# a block that is not the mix estimates r for a population the confirmatory set
# does not contain, which is choosing the mix from the data through the back
# door.
COMMITTED_BLOCK_MIX: dict[int, dict[str, int]] = {
    1: {"natural-language": 21, "citation-anchored": 5, "cross-section": 4},
    2: {"natural-language": 21, "citation-anchored": 4, "cross-section": 5},
    3: {"natural-language": 21, "citation-anchored": 5, "cross-section": 4},
    4: {"natural-language": 21, "citation-anchored": 4, "cross-section": 5},
    5: {"natural-language": 21, "citation-anchored": 5, "cross-section": 4},
}

load_env()
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)


def load_block(path: Path, block: int) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [c for c in cases if c.get("block") == block]


def check_mix(cases: list[dict[str, Any]], block: int) -> str | None:
    """None if the block matches its pre-committed composition, else why not."""
    expected = COMMITTED_BLOCK_MIX.get(block)
    if expected is None:
        return f"block {block} has no committed mix"
    actual: dict[str, int] = {}
    for case in cases:
        shape = str(case.get("shape", ""))
        actual[shape] = actual.get(shape, 0) + 1
    if actual != expected:
        return f"block {block} composition {actual} != committed {expected}"
    return None


def _hit(rank: int | None) -> bool:
    return rank is not None and rank <= K


def case_record(case_id: str, shape: str, rank_a: int | None, rank_b: int | None) -> dict[str, Any]:
    """**The blinding boundary.** Direction goes in; direction does not come out.

    `rank_a != rank_b` is asked only through `_hit(...) != _hit(...)`, which is
    symmetric in its arguments. No expression on this path asks *which* arm hit,
    so there is no `b` and no `c` anywhere downstream to leak, redact or forget
    to redact.
    """
    return {"id": case_id, "shape": shape, "discordant": _hit(rank_a) != _hit(rank_b)}


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    n_discordant = sum(1 for r in records if r["discordant"])
    shapes: dict[str, int] = {}
    for record in records:
        shapes[record["shape"]] = shapes.get(record["shape"], 0) + 1
    return {
        "n": n,
        "n_discordant": n_discordant,
        "r": round(n_discordant / n, 4) if n else 0.0,
        "shape_composition": dict(sorted(shapes.items())),
    }


def sizing(n_discordant: int, n: int) -> dict[str, Any]:
    """Implied N, from the discordance rate alone.

    Every input here is a function of `n` and `n_discordant`. Nothing in this
    function can see, or be made to see, which arm won.
    """
    required = {
        "floor": MIN_DISCORDANT_FOR_ANY_REJECTION,
        "power_0.5": min_discordant_for_power(0.5, THETA),
        "power_0.8": min_discordant_for_power(0.8, THETA),
    }
    r = n_discordant / n if n else 0.0
    low, high = clopper_pearson(n_discordant, n)

    def implied(rate: float) -> dict[str, int | None]:
        return {k: (math.ceil(v / rate) if rate > 0 else None) for k, v in required.items()}

    return {
        "theta_assumed": THETA,
        "required_discordant": required,
        "r_point": round(r, 4),
        "r_ci95": [round(low, 4), round(high, 4)],
        # N at the CI's LOWER rate is the pessimistic N and is therefore the
        # large one; None means the rate is 0 and no finite N reaches the target.
        "N_at_r_point": implied(r),
        "N_at_r_ci_low": implied(low),
        "N_at_r_ci_high": implied(high),
    }


async def measure(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    engine = create_async_engine(CORPUS_URL, pool_size=4, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chunk_count = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    embedder = OpenAIEmbeddingClient()
    retriever = Retriever(factory, embedder)

    from rag_qa.retrieval.search import fulltext_search

    records: list[dict[str, Any]] = []
    silent_branch = 0
    for case in cases:
        prefix = case["expected_section_prefix"]
        async with factory() as session:
            if not await fulltext_search(session, case["question"], None):
                silent_branch += 1
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
        # The two ranks exist for exactly one expression and are then out of
        # scope. They are never collected, never returned and never written.
        records.append(
            case_record(
                case["id"],
                str(case.get("shape", "")),
                _rank_of(hybrid, prefix),
                _rank_of(vector_only, prefix),
            )
        )
    await engine.dispose()
    return records, chunk_count, silent_branch


def _rank_of(chunks: list[RetrievedChunk], prefix: str) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if chunk.section_path.startswith(prefix):
            return rank
    return None


def git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.stdout.strip() or "unknown"


async def run(block: int) -> int:
    if not CONFIRMATORY_SET.exists():
        print(f"no confirmatory set at {CONFIRMATORY_SET}", file=sys.stderr)
        return 2
    cases = load_block(CONFIRMATORY_SET, block)
    problem = check_mix(cases, block)
    if problem:
        print(f"refusing to run: {problem}", file=sys.stderr)
        return 3

    records, chunk_count, silent_branch = await measure(cases)
    summary = summarise(records)
    plan = sizing(int(summary["n_discordant"]), int(summary["n"]))

    print(f"\ncorpus: {chunk_count} chunks   block {block}, k = {K}\n")
    print(f"  n                            {summary['n']}")
    print(f"  n_discordant                 {summary['n_discordant']}")
    print(f"  r                            {summary['r']}")
    print(f"  r 95% CI                     {plan['r_ci95']}")
    print(f"  questions with silent FTS    {silent_branch}")
    print(f"\n  required discordant pairs    {plan['required_discordant']}")
    print(f"  implied N at r               {plan['N_at_r_point']}")
    print(f"  implied N at CI low  (worse) {plan['N_at_r_ci_low']}")
    print(f"  implied N at CI high (better){plan['N_at_r_ci_high']}")
    print("\n  b and c are not computed by this script and are not in the artifact.")

    out = REPO_ROOT / "evals" / f"interim-block-{block}.json"
    payload = {
        "interim_id": f"confirmatory-block-{block}",
        "measured_at": datetime.now(UTC).date().isoformat(),
        "git_sha": git_sha(),
        "corpus_chunks": chunk_count,
        "k": K,
        "blinded": True,
        "questions_with_no_fulltext_candidates": silent_branch,
        "summary": summary,
        "sizing": plan,
        "per_case": records,
        "blinding_note": (
            "b and c are never computed on this path (SPEC-007 AC-17). Per-case "
            "records carry only id, shape and discordance, so the split cannot be "
            "reconstructed from this artifact; the measured ranks were discarded "
            "and the confirmatory run re-measures from scratch."
        ),
        "not_a_claim": (
            "This is a sizing estimate, not a result. No comparison is decided here "
            "and no arm is reported as winning."
        ),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {out.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    return asyncio.run(run(int(os.environ.get("RAG_QA_INTERIM_BLOCK", "1"))))


if __name__ == "__main__":
    sys.exit(main())
