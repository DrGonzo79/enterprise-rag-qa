"""The confirmatory analysis (SPEC-007 Key decision 12). **This unblinds the set.**

Runs once, against the 120 questions of `evals/retrieval_confirmatory.jsonl`,
after the stopping rule fired at 23 discordant pairs. Everything it reports was
fixed before it ran: the metric, `k`, the test, the sidedness, alpha, the floor, the
shape mix, the stopping rule, which analysis is primary, and the `not_a_claim`.

**Primary: pruning OFF** — the committed default (`MAX_LEXEME_CHUNK_FRACTION =
None`) that every block was measured under. **Secondary: pruning ON**, reported
alongside and never permitted to displace the primary (amendment 8).

Usage:
    uv run python -m scripts.confirmatory
"""

import asyncio
import json
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
from rag_qa.retrieval import search as search_module
from rag_qa.retrieval.search import vector_search
from rag_qa.retrieval.service import Retriever
from rag_qa.retrieval.types import RetrievedChunk
from scripts.interim_r import K, load_block, unresolvable_prefixes
from scripts.mcnemar import MIN_DISCORDANT_FOR_ANY_REJECTION, mcnemar_exact_two_sided
from scripts.query_plan import EVAL_SERVER_SETTINGS, observed_vector_plan
from scripts.section_match import matches_section

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIRMATORY_SET = REPO_ROOT / "evals" / "retrieval_confirmatory.jsonl"
OUT = REPO_ROOT / "evals" / "confirmatory-result.json"
ALPHA = 0.05
BLOCKS = (1, 2, 3, 4)
PRUNING_SECONDARY = 0.25

load_env()
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)

PREREGISTRATION = {
    "primary_metric": "recall@8",
    "k": K,
    "lever": "hybrid (RRF over vector + full-text) versus vector-only",
    "pairing_unit": "one question; discordant = exactly one arm succeeds at k=8",
    "test": "McNemar exact binomial, conditional on n = b + c",
    "sidedness": "two-sided",
    "alpha": ALPHA,
    "floor": MIN_DISCORDANT_FOR_ANY_REJECTION,
    "conclusive_when": "n_discordant >= 6 AND p < alpha",
    "stopping_rule": "author until n_discordant >= 23, cap N = 200, evaluated at block boundaries",
    "primary_analysis": "pruning OFF (MAX_LEXEME_CHUNK_FRACTION = None)",
    "secondary_analysis": "pruning ON (0.25), never permitted to displace the primary",
}

NOT_A_CLAIM = (
    "This is a retrieval-only comparison at k = 8, on a 358-chunk corpus of three "
    "documents, scored against section-prefix labels that are NOT human-verified. It "
    "does not measure answer quality, groundedness, or refusal. A significant result "
    "establishes that hybrid and vector-only differ in recall@8 on this question mix "
    "and this corpus; it does not establish that the difference generalises to another "
    "corpus, another k, another question mix, or to any user-visible quality. An "
    "inconclusive result does not establish equivalence — it establishes that this set, "
    "at this size, could not distinguish the arms."
)

DEVIATIONS = [
    {
        "what": "Gold labels are not human-verified",
        "detail": (
            "SPEC-004 requires a human to verify each retrieval label. Every case "
            "carries human_verified: false — labels were verified against corpus text "
            "by the authoring agent, not by the repository owner. This is the largest "
            "deviation on this list and it bounds every figure below."
        ),
    },
    {
        "what": "Fixed N replaced by inverse sampling mid-flight",
        "detail": (
            "KD-12 amendment 7. The fixed-N framing compared a realized count against "
            "a required one as though n_discordant were N x r. Corrected to: author "
            "until 23 pairs, cap 200. The trigger was a design error, not an interim "
            "number, and the correction holds whatever r turned out to be."
        ),
    },
    {
        "what": "Four interim looks where one was pre-committed",
        "detail": (
            "KD-12 amendment 5 pre-committed a single interim at N = 30. Looks were "
            "taken at 30, 60, 90 and 120, each owner-asked. All were blinded — b and c "
            "were never computed — so b ~ Binomial(n, 1/2) conditional on the realized "
            "n is untouched. What was spent is the negotiation risk, not alpha."
        ),
    },
    {
        "what": "Stopping evaluated at block boundaries, not per question",
        "detail": (
            "Refinement chosen by the implementer, not instructed: stopping mid-block "
            "would break the committed shape mix at the moment the set freezes. Cost "
            "E[N] = 165 against 153 for exact stopping. It happened to cost nothing — "
            "the rule fired at exactly 23 with no overshoot."
        ),
    },
    {
        "what": "The 26-question smoke set was excluded",
        "detail": (
            "SPEC-004's cross-spec note called it the seed of the retrieval set. "
            "KD-12 amendment 5 excluded it: all 26 were authored with the expected "
            "section in hand, the property that saturated recall@8."
        ),
    },
    {
        "what": "Eval runs pin enable_seqscan = off; the API does not",
        "detail": (
            "Today both settings yield an exact plan and identical results, verified by "
            "re-measuring three blocks under each, so the numbers characterise the "
            "configuration the demo runs in. This stops being true if SPEC-004 KD-7a is "
            "ever applied."
        ),
    },
    {
        "what": "Single-arm difficulty drifts downward across blocks",
        "detail": (
            "Excluding block 2 the vector-only MRR@8 falls monotonically "
            "(0.6511, 0.5879, 0.5489); Spearman rho = -0.8 over four blocks, p = 0.33. "
            "The band did not fire, and amendment 6 stated in advance that 30 per block "
            "is close to blind to a slope. Reported as a limitation, not a deviation."
        ),
    },
]


def _rank_of(chunks: list[RetrievedChunk], prefix: str) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if matches_section(prefix, chunk.section_path):
            return rank
    return None


def _hit(rank: int | None) -> bool:
    return rank is not None and rank <= K


async def score(cases: list[dict[str, Any]], factory: Any, embedder: Any) -> list[dict[str, Any]]:
    retriever = Retriever(factory, embedder)
    rows: list[dict[str, Any]] = []
    for case in cases:
        prefix = case["expected_section_prefix"]
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
                "block": case["block"],
                "shape": case["shape"],
                "document": case["document"],
                "hybrid_rank": _rank_of(hybrid, prefix),
                "vector_rank": _rank_of(vector_only, prefix),
            }
        )
    return rows


def analyse(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    b = sum(1 for r in rows if _hit(r["hybrid_rank"]) and not _hit(r["vector_rank"]))
    c = sum(1 for r in rows if _hit(r["vector_rank"]) and not _hit(r["hybrid_rank"]))
    both = sum(1 for r in rows if _hit(r["hybrid_rank"]) and _hit(r["vector_rank"]))
    n = len(rows)
    n_discordant = b + c
    p = mcnemar_exact_two_sided(b, c)
    conclusive = n_discordant >= MIN_DISCORDANT_FOR_ANY_REJECTION and p < ALPHA
    if not conclusive:
        outcome = "inconclusive"
    elif b > c:
        outcome = "hybrid wins"
    else:
        outcome = "vector-only wins"
    return {
        "analysis": label,
        "n": n,
        "b_hybrid_only": b,
        "c_vector_only": c,
        "both": both,
        "neither": n - b - c - both,
        "n_discordant": n_discordant,
        "recall_at_8_hybrid": round((b + both) / n, 4),
        "recall_at_8_vector_only": round((c + both) / n, 4),
        "p_two_sided_exact": round(p, 6),
        "alpha": ALPHA,
        "outcome": outcome,
    }


async def run() -> int:
    cases = [case for block in BLOCKS for case in load_block(CONFIRMATORY_SET, block)]
    if len(cases) != 120:
        print(f"expected 120 cases, found {len(cases)}", file=sys.stderr)
        return 2

    engine = create_async_engine(
        CORPUS_URL,
        pool_size=4,
        max_overflow=2,
        connect_args={"server_settings": EVAL_SERVER_SETTINGS},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chunk_count = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
        query_plan = await observed_vector_plan(session)
        problems = await unresolvable_prefixes(session, cases)
    if problems:
        print("unusable gold labels:\n  " + "\n  ".join(problems), file=sys.stderr)
        await engine.dispose()
        return 3

    embedder = OpenAIEmbeddingClient()

    # PRIMARY: the committed default. Asserted rather than assumed, because the
    # whole pre-registration is written against this value.
    assert search_module.MAX_LEXEME_CHUNK_FRACTION is None
    primary_rows = await score(cases, factory, embedder)
    primary = analyse(primary_rows, "primary — pruning OFF (committed default)")

    # SECONDARY: reported alongside, never permitted to displace the primary.
    search_module.MAX_LEXEME_CHUNK_FRACTION = PRUNING_SECONDARY
    try:
        secondary_rows = await score(cases, factory, embedder)
    finally:
        search_module.MAX_LEXEME_CHUNK_FRACTION = None
    secondary = analyse(secondary_rows, f"secondary — pruning ON ({PRUNING_SECONDARY})")

    await engine.dispose()

    for result in (primary, secondary):
        print(f"\n=== {result['analysis']} ===")
        print(f"  b (hybrid only)   {result['b_hybrid_only']}")
        print(f"  c (vector only)   {result['c_vector_only']}")
        print(f"  n_discordant      {result['n_discordant']}")
        print(f"  p (exact, 2-sided){result['p_two_sided_exact']:>10}")
        print(f"  outcome at a={ALPHA}  {result['outcome']}")
        print(
            f"  recall@8          hybrid {result['recall_at_8_hybrid']} / "
            f"vector-only {result['recall_at_8_vector_only']}"
        )

    payload = {
        "measured_at": datetime.now(UTC).date().isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip(),
        "corpus_chunks": chunk_count,
        "query_plan": query_plan,
        "preregistration": PREREGISTRATION,
        "primary": primary,
        "secondary": secondary,
        "per_case_primary": primary_rows,
        "per_case_secondary": secondary_rows,
        "deviations": DEVIATIONS,
        "not_a_claim": NOT_A_CLAIM,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {OUT.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
