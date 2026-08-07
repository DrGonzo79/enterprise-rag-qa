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
from scripts.query_plan import EVAL_SERVER_SETTINGS, observed_vector_plan
from scripts.section_match import matches_section, straddles_a_component

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
    # Blocks 6 and 7 exist because the design is now inverse sampling with a cap
    # at 200 (amendment 7), not a fixed 150. Six blocks of 30 plus one of 20
    # compose to 200 at EXACTLY 140/30/30 -- 70/15/15 with no rounding left over.
    6: {"natural-language": 21, "citation-anchored": 4, "cross-section": 5},
    7: {"natural-language": 14, "citation-anchored": 3, "cross-section": 3},
}

# --- The stopping rule (SPEC-007 KD-12 amendment 7) --------------------------
#
# Author until n_discordant reaches TARGET, capped at N_CAP. Inverse sampling
# delivers the count the power calculation needs instead of its expectation:
# a fixed N = 150 at r = 0.15 has mean 22.5 discordant pairs with SD 4.4, so it
# reaches 23 about half the time.
#
# Valid under exactly the blinding already in place: the exact test conditions
# on n = b + c, and under H0 the direction of each discordant pair is an
# independent fair coin regardless of WHICH questions were discordant. A rule
# that reads only the discordance indicators therefore leaves b ~ Binomial(n, 1/2)
# conditional on the realized n. The four structural blinds are that condition.
TARGET_DISCORDANT = 23
N_CAP = 200

# --- The single-arm difficulty proxy (SPEC-007 KD-12 amendment 6) ------------
#
# Per-block shape quotas control drift in the dimension that was committed and
# nothing in difficulty. The mechanism they miss: over five blocks an author
# gets better at writing questions that discriminate. That does not threaten
# validity -- McNemar is paired and heterogeneity is fine -- but it erodes the
# representativeness that is the entire justification for 70/15/15, and it is
# tuning toward `r` through a door no rule guards.
#
# **VECTOR-ONLY, and the single arm is not a preference.** Publishing both
# arms' recall beside `n_discordant` would determine the split exactly:
# hybrid_hits = both + b, vector_hits = both + c, n_discordant = b + c, so
# b = (n_discordant + hybrid_hits - vector_hits) / 2. The hybrid arm's recall is
# therefore never computed here.
#
# **The banded proxy is MRR@8, not recall@8, and recall@8 was the first
# choice.** Block 1's vector-only recall@8 is 0.90, so a two-sided band of any
# useful width runs off the end of the scale — ±0.20 puts the upper edge at
# 1.10, and even a noise-derived ±0.15 puts it at 1.05. **A band that only one
# direction can ever breach is a one-sided band wearing a two-sided label**, and
# the saturation doing it is the same saturation that made recall@8 useless on
# the smoke set. MRR@8 sits at 0.65, moves continuously, and sees a block whose
# gold chunks all slid from rank 1 to rank 6 — which recall@8 cannot.
#
# Band width is derived from block 1's own dispersion rather than chosen:
# per-question reciprocal rank has SD 0.381 over the block, so the standard
# error of a 30-question mean is 0.0696 and of the difference between two
# blocks 0.0984. 1.96 x that is 0.1928, rounded **inward** to 0.19 — slightly
# tighter than sampling noise alone would justify, because a false alarm costs
# one paragraph and a missed drift costs the set's representativeness.
DIFFICULTY_BAND = 0.19
# Block 1's measured value, recorded before the band was set and before block 2
# existed. Anchoring on a measured block rather than on a target is the point:
# the question is whether later blocks drift from the one the mix was first
# authored against, not whether they hit a number someone hoped for.
BLOCK_1_REFERENCE_MRR = 0.6511
# Reported beside it, with no band, for the reason above.
BLOCK_1_REFERENCE_RECALL = 0.9

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


def single_arm_difficulty(ranks: list[int | None]) -> dict[str, Any]:
    """How hard the block is for **one** arm, aggregated over the whole block.

    Aggregate, never per case, and that is load-bearing rather than tidy: a
    per-case vector outcome published beside a per-case discordance flag would
    give the split away one question at a time — a discordant case whose vector
    arm hit is a `c`, one whose vector arm missed is a `b`. The aggregate leaves
    exactly one free parameter (see `sizing`'s callers and AC-17).
    """
    n = len(ranks)
    hits = [r for r in ranks if r is not None and r <= K]
    histogram = {str(rank): sum(1 for r in hits if r == rank) for rank in range(1, K + 1)}
    histogram["miss"] = n - len(hits)
    return {
        "arm": "vector-only",
        "recall_at_8": round(len(hits) / n, 4) if n else 0.0,
        # Finer-grained than recall@8, which moves in steps of 1/30 and cannot
        # see a block whose gold chunks all slid from rank 1 to rank 7.
        "mrr_at_8": round(sum(1 / r for r in hits) / n, 4) if n else 0.0,
        "gold_rank_histogram": histogram,
    }


def drift_breach(mrr: float, block: int) -> str | None:
    """None if the block sits inside the committed band, else the breach text.

    A breach is not an error and does not stop the run — it is recorded as a
    deviation under AC-14, which is the honest handling: the band is a tripwire
    for a conversation, not a gate on authoring.
    """
    if block == 1:
        return None
    # Rounded to the precision the proxy is reported at, so a block sitting
    # exactly on the band edge is inside it rather than inside-or-out depending
    # on binary floating point.
    delta = round(mrr - BLOCK_1_REFERENCE_MRR, 4)
    if abs(delta) > DIFFICULTY_BAND:
        return (
            f"block {block} vector-only MRR@8 = {mrr:.4f}, "
            f"{delta:+.4f} from block 1's {BLOCK_1_REFERENCE_MRR} "
            f"(band ±{DIFFICULTY_BAND}) — record a deviation (AC-14)"
        )
    return None


def stopping_state(n: int, n_discordant: int) -> dict[str, Any]:
    """Where the set stands against the stopping rule, in questions.

    Evaluated at **block boundaries**, not per question: stopping mid-block
    would break the shape mix at the moment the set is frozen, and the mix is
    the thing amendment 4 chose a priori. The price is a small overshoot --
    E[N] = 165 against 153 for exact stopping at r = 0.15 -- and overshoot only
    adds power.
    """
    remaining_discordant = max(0, TARGET_DISCORDANT - n_discordant)
    r_hat = n_discordant / n if n else 0.0
    expected_more = math.ceil(remaining_discordant / r_hat) if r_hat > 0 else None
    room = N_CAP - n
    if remaining_discordant == 0:
        verdict = f"STOP: {n_discordant} >= {TARGET_DISCORDANT} discordant pairs at N = {n}"
    elif room <= 0:
        verdict = (
            f"STOP: cap N = {N_CAP} reached with {n_discordant} pairs — report as underpowered"
        )
    else:
        verdict = (
            f"CONTINUE: {remaining_discordant} more pairs needed, {room} questions left to cap"
        )
    return {
        "target_discordant": TARGET_DISCORDANT,
        "n_cap": N_CAP,
        "n_so_far": n,
        "n_discordant_so_far": n_discordant,
        "discordant_remaining": remaining_discordant,
        # At the CURRENT r estimate, which is itself uncertain -- this is a
        # planning figure, not a promise about where the set will stop.
        "expected_questions_remaining": expected_more,
        "questions_remaining_to_cap": room,
        "verdict": verdict,
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


async def unresolvable_prefixes(session: Any, cases: list[dict[str, Any]]) -> list[str]:
    """Gold labels that name no section, or that match one mid-word.

    A prefix resolving to nothing scores as a miss for **both** arms on every
    run, forever, and looks exactly like a hard question. A prefix resolving too
    *widely* is worse, because it looks exactly like an easy one. Neither can be
    found by staring at the number afterwards, so both are refused before any
    embedding is bought.
    """
    found = await session.execute(text("SELECT DISTINCT section_path FROM chunks"))
    all_paths = [row[0] for row in found]
    problems: list[str] = []
    for case in cases:
        for key in ("expected_section_prefix", "also_contains"):
            prefix = case.get(key)
            if prefix is None:
                continue
            # No SQL LIKE: `_` and `%` are wildcards there, so a label
            # containing either would match more paths than it names -- the same
            # class of bug one level down, and it would corrupt the check that
            # exists to catch that class. 263 distinct paths; filtering in
            # Python is exact and free.
            paths = [p for p in all_paths if p.startswith(str(prefix))]
            if not paths:
                problems.append(f"{case['id']}.{key}: matches no section — {prefix}")
                continue
            straddled = [p for p in paths if straddles_a_component(str(prefix), p)]
            if straddled:
                problems.append(
                    f"{case['id']}.{key}: matches mid-word — {prefix} also matches "
                    f"{len(straddled)} other section(s), e.g. {straddled[0]}"
                )
    return problems


async def measure(
    cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, dict[str, Any], dict[str, Any]]:
    engine = create_async_engine(
        CORPUS_URL,
        pool_size=4,
        max_overflow=2,
        connect_args={"server_settings": EVAL_SERVER_SETTINGS},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chunk_count = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
        plan = await observed_vector_plan(session)
        # BEFORE the embedder is constructed and before any call is billed: a
        # gold label that names no section, or names one mid-word, is refused
        # while it is still free to refuse it.
        missing = await unresolvable_prefixes(session, cases)
    if missing:
        await engine.dispose()
        raise ValueError("unusable gold labels:\n  " + "\n  ".join(missing))
    embedder = OpenAIEmbeddingClient()
    retriever = Retriever(factory, embedder)

    from rag_qa.retrieval.search import fulltext_search

    records: list[dict[str, Any]] = []
    vector_ranks: list[int | None] = []
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
        # The HYBRID rank exists for exactly one expression and is then out of
        # scope: never collected, never returned, never written. The vector
        # rank is collected, because the difficulty proxy needs it -- and it is
        # collected into a flat list that is aggregated before it leaves this
        # function, so no per-case vector outcome is ever paired back to a
        # per-case discordance flag.
        vector_rank = _rank_of(vector_only, prefix)
        vector_ranks.append(vector_rank)
        records.append(
            case_record(
                case["id"],
                str(case.get("shape", "")),
                _rank_of(hybrid, prefix),
                vector_rank,
            )
        )
    await engine.dispose()
    return records, chunk_count, silent_branch, single_arm_difficulty(vector_ranks), plan


def _rank_of(chunks: list[RetrievedChunk], prefix: str) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if matches_section(prefix, chunk.section_path):
            return rank
    return None


def cumulative(up_to: int, current: dict[str, Any], current_mrr: float) -> dict[str, Any] | None:
    """Pool the blocks measured so far, from their artifacts rather than by re-running.

    The artifacts carry per-case discordance and nothing else, so pooling them
    is as blind as producing them was — there is no split on disk to pool.

    **Each pooled look is a deviation from amendment 5's one-interim rule and is
    recorded as one (AC-14).** The statistical argument is untouched: `r` is a
    nuisance parameter at every look, not only the first. What the one-look rule
    was protecting against is negotiation with the set, and that cost is paid
    per look regardless of what the arithmetic permits.
    """
    # The CURRENT block is passed in rather than read back, because its artifact
    # is written after this runs. Reading only the files gave a "cumulative"
    # that silently excluded the block being measured -- a total that was wrong
    # by exactly the newest data, which is the one direction nobody checks.
    summaries: list[tuple[str, int, int, float | None]] = []
    for block in range(1, up_to):
        path = REPO_ROOT / "evals" / f"interim-block-{block}.json"
        if path.exists():
            b = json.loads(path.read_text(encoding="utf-8"))
            summaries.append(
                (
                    str(b["interim_id"]),
                    int(b["summary"]["n"]),
                    int(b["summary"]["n_discordant"]),
                    b.get("difficulty", {}).get("mrr_at_8"),
                )
            )
    summaries.append(
        (
            f"confirmatory-block-{up_to}",
            int(current["n"]),
            int(current["n_discordant"]),
            current_mrr,
        )
    )
    if len(summaries) < 2:
        return None
    n = sum(s[1] for s in summaries)
    n_discordant = sum(s[2] for s in summaries)
    return {
        "blocks": [s[0] for s in summaries],
        "per_block_n": [s[1] for s in summaries],
        "per_block_n_discordant": [s[2] for s in summaries],
        "per_block_mrr_at_8": [s[3] for s in summaries],
        "n": n,
        "n_discordant": n_discordant,
        "r": round(n_discordant / n, 4),
        "sizing": sizing(n_discordant, n),
        "stopping_rule": stopping_state(n, n_discordant),
        "deviation": (
            "Amendment 5 pre-committed ONE interim look, at 30. This is look "
            f"{len(summaries)}, owner-asked. Recorded under AC-14. Blinding is "
            "unchanged and so is the Type I argument; what is spent is the "
            "negotiation risk the one-look rule was reserving."
        ),
    }


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

    records, chunk_count, silent_branch, difficulty, query_plan = await measure(cases)
    summary = summarise(records)
    plan = sizing(int(summary["n_discordant"]), int(summary["n"]))
    breach = drift_breach(float(difficulty["mrr_at_8"]), block)

    print(f"\ncorpus: {chunk_count} chunks   block {block}, k = {K}\n")
    print(f"  n                            {summary['n']}")
    print(f"  n_discordant                 {summary['n_discordant']}")
    print(f"  r                            {summary['r']}")
    print(f"  r 95% CI                     {plan['r_ci95']}")
    print(f"  questions with silent FTS    {silent_branch}")
    print("\n  query plan (recorded, not assumed):")
    print(f"    nodes                      {' <- '.join(query_plan['nodes'])}")
    print(f"    exact / HNSW used          {query_plan['exact']} / {query_plan['hnsw_index_used']}")
    print("\n  difficulty proxy (vector-only, single arm):")
    print(f"    recall@8                   {difficulty['recall_at_8']}")
    print(f"    MRR@8                      {difficulty['mrr_at_8']}")
    print(f"    gold rank histogram        {difficulty['gold_rank_histogram']}")
    print(f"    drift vs block 1           {'IN BAND' if breach is None else 'BREACH'}")
    if breach:
        print(f"    {breach}")
    print(f"\n  required discordant pairs    {plan['required_discordant']}")
    print(f"  implied N at r               {plan['N_at_r_point']}")
    print(f"  implied N at CI low  (worse) {plan['N_at_r_ci_low']}")
    print(f"  implied N at CI high (better){plan['N_at_r_ci_high']}")
    print("\n  b and c are not computed by this script and are not in the artifact.")

    pooled = cumulative(block, summary, float(difficulty["mrr_at_8"]))
    if pooled:
        blocks_seen = len(pooled["blocks"])
        print(f"\n  cumulative over {blocks_seen} blocks (AC-14 deviation, see artifact):")
        print(f"    n / n_discordant           {pooled['n']} / {pooled['n_discordant']}")
        print(f"    r                          {pooled['r']}")
        print(f"    per-block n_discordant     {pooled['per_block_n_discordant']}")
        print(f"    per-block MRR@8            {pooled['per_block_mrr_at_8']}")
        print(f"    implied N                  {pooled['sizing']['N_at_r_point']}")
        stop = pooled["stopping_rule"]
        print(f"\n  stopping rule (amendment 7): {TARGET_DISCORDANT} pairs, cap N = {N_CAP}")
        print(f"    pairs still needed         {stop['discordant_remaining']}")
        print(f"    questions expected at r    {stop['expected_questions_remaining']}")
        print(f"    questions left to the cap  {stop['questions_remaining_to_cap']}")
        print(f"    {stop['verdict']}")

    out = REPO_ROOT / "evals" / f"interim-block-{block}.json"
    payload = {
        "interim_id": f"confirmatory-block-{block}",
        "measured_at": datetime.now(UTC).date().isoformat(),
        "git_sha": git_sha(),
        "corpus_chunks": chunk_count,
        "k": K,
        "blinded": True,
        "questions_with_no_fulltext_candidates": silent_branch,
        "query_plan": query_plan,
        "difficulty": difficulty,
        "difficulty_band": {
            "reference_block": 1,
            "banded_metric": "mrr_at_8",
            "reference_mrr_at_8": BLOCK_1_REFERENCE_MRR,
            "band": DIFFICULTY_BAND,
            "reference_recall_at_8": BLOCK_1_REFERENCE_RECALL,
            "recall_at_8_is_unbanded_because": (
                "block 1 sits at 0.90, so any useful two-sided band runs past 1.0 "
                "and only the harder direction could ever breach it"
            ),
            "breach": breach,
        },
        "summary": summary,
        "sizing": plan,
        "cumulative": pooled,
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
