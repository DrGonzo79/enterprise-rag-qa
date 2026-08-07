"""The near-miss refusal pilot's instrument (SPEC-007, `evals/prereg-refusal-nearmiss.md`).

Two modes, and the split is the pre-registration's §3.1 made mechanical:

- `verify` retrieves and prints the top-k chunks **and nothing else**. It cannot
  reach a provider, so a question can be checked for "is the answer genuinely
  absent from what the model will see" without any verdict existing yet.
- `run` retrieves, generates, and records everything. It refuses to start unless
  every question in the file is marked verified.

The ordering matters more than it looks: §3.5 freezes the question text before
the first generation call, and a `verify` mode that could also generate is a
mode that lets an author read a verdict and then reword.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.db.models import Chunk, QueryLog, SpendSource
from rag_qa.env import load_env
from rag_qa.generation.clients.anthropic import AnthropicClient
from rag_qa.generation.prompt import PROMPT_VERSION
from rag_qa.generation.service import Generator
from rag_qa.ingest.embedder import OpenAIEmbeddingClient
from rag_qa.retrieval import Retriever
from scripts.query_plan import EVAL_SERVER_SETTINGS, observed_vector_plan

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = REPO_ROOT / "evals" / "refusal_pilot.jsonl"
RESULT = REPO_ROOT / "evals" / f"refusal-pilot-result{os.environ.get('PILOT_RUN', '')}.json"
K = 8

# §4: the run stops rather than silently exceeding what was pre-registered.
# Raised from 0.40 by deviation 1 after the original fired at question 24: the
# input-token estimate behind it was 2.6x low (7,830 measured against 3,000
# assumed). The arithmetic was corrected; the bound was not loosened to fit.
COST_BOUND_USD = Decimal("0.70")


def _load() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _engine(database_url: str) -> Any:
    return create_async_engine(database_url, connect_args={"server_settings": EVAL_SERVER_SETTINGS})


async def _verify(database_url: str, only: str | None) -> None:
    engine = _engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    retriever = Retriever(factory, OpenAIEmbeddingClient())
    try:
        for case in _load():
            if only and case["id"] != only:
                continue
            chunks = await retriever.retrieve(case["question"], k=K)
            print(f"\n{'=' * 78}\n{case['id']} [{case['arm']}/{case['shape']}] {case['question']}")
            print(f"  absent: {case.get('what_is_absent', '-')}")
            for rank, chunk in enumerate(chunks, start=1):
                print(f"\n  [{rank}] {chunk.section_path}")
                print(f"      {chunk.text[:900]}")
    finally:
        await engine.dispose()


async def _recover(database_url: str) -> None:
    """Rebuild the artifact from `query_log` for questions already answered.

    Needed exactly once, because the bound's first firing threw the run's
    in-memory records away. It is not a general escape hatch: it can only
    reconstruct what the log already holds, and it re-asks nothing.
    """
    engine = _engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    records: list[dict[str, Any]] = []
    total = Decimal("0")
    try:
        async with factory() as session:
            plan = await observed_vector_plan(session)
            identity = ""
            for case in _load():
                row = (
                    await session.execute(
                        select(QueryLog)
                        .where(QueryLog.question == case["question"])
                        .order_by(QueryLog.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if row is None:
                    continue
                identity = f"{row.provider}:{row.model}"
                paths = dict(
                    (
                        await session.execute(
                            select(Chunk.id, Chunk.section_path).where(
                                Chunk.id.in_(row.retrieved_chunk_ids)
                            )
                        )
                    ).all()
                )
                ordered = [paths[chunk_id] for chunk_id in row.retrieved_chunk_ids]
                markers = sorted({int(m) for m in re.findall(r"\[(\d+)\]", row.answer_text)})
                total += Decimal(str(row.cost_usd))
                records.append(
                    {
                        "id": case["id"],
                        "arm": case["arm"],
                        "shape": case["shape"],
                        "document": case["document"],
                        "question": case["question"],
                        "what_is_absent": case.get("what_is_absent"),
                        "verdict": row.verdict,
                        "refused": row.verdict == "insufficient_evidence",
                        "answer": row.answer_text,
                        "retrieved_section_paths": ordered,
                        "cited_section_paths": [
                            ordered[m - 1] for m in markers if 1 <= m <= len(ordered)
                        ],
                        "prompt_tokens": row.prompt_tokens,
                        "completion_tokens": row.completion_tokens,
                        "cost_usd": str(row.cost_usd),
                        "recovered_from_query_log": True,
                    }
                )
    finally:
        await engine.dispose()
    _write(records, total, plan, identity, None, complete=False)


async def _run(database_url: str) -> None:
    cases = _load()
    unverified = [case["id"] for case in cases if not case.get("verified")]
    if unverified:
        raise SystemExit(f"not verified, so `run` will not start: {unverified}")

    engine = _engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    retriever = Retriever(factory, OpenAIEmbeddingClient())
    # SpendSource.EVAL: this presses the monthly ceiling, which is the invoice,
    # and not the daily visitor one, whose job is shaping visitor burst.
    generator = Generator(AnthropicClient(), session_factory=factory, source=SpendSource.EVAL)

    # Resume, rather than restart. Re-running a question whose verdict is
    # already known and keeping the second answer is choosing between two draws,
    # which is exactly what a pre-registration exists to stop. So anything
    # already in the artifact is skipped and never re-asked.
    previous = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else {}
    records: list[dict[str, Any]] = list(previous.get("records", []))
    done = {record["id"] for record in records}
    total = Decimal(previous.get("cost_usd", "0"))
    answer = None
    try:
        async with factory() as session:
            plan = await observed_vector_plan(session)
        for case in cases:
            if case["id"] in done:
                continue
            chunks = await retriever.retrieve(case["question"], k=K)
            answer = await generator.answer(case["question"], chunks)
            total += answer.cost_usd
            records.append(
                {
                    "id": case["id"],
                    "arm": case["arm"],
                    "shape": case["shape"],
                    "document": case["document"],
                    "question": case["question"],
                    "what_is_absent": case.get("what_is_absent"),
                    "verdict": answer.verdict.value,
                    "refused": answer.verdict.value == "insufficient_evidence",
                    "answer": answer.text,
                    "retrieved_section_paths": [chunk.section_path for chunk in chunks],
                    "cited_section_paths": [citation.section_path for citation in answer.citations],
                    "prompt_tokens": answer.prompt_tokens,
                    "completion_tokens": answer.completion_tokens,
                    "cost_usd": str(answer.cost_usd),
                    # KD-7 amendment 1: both verdicts, so the disagreement rate
                    # between the header and the model's considered verdict is a
                    # number rather than something nobody could have seen.
                    "provisional_verdict": answer.provisional_verdict.value,
                    "verdict_reconciled": answer.verdict_reconciled,
                }
            )
            print(f"{case['id']:>6} {case['arm']} {answer.verdict.value:<22} ${total}")
            if total > COST_BOUND_USD:
                halted = f"cost {total} exceeded the pre-registered bound {COST_BOUND_USD}"
                break
        else:
            halted = None
    finally:
        await engine.dispose()

    # **Written even when the bound halts the run**, and this is a defect the
    # bound found in its own first firing: the original code raised here, and
    # threw away 24 answers that had already been paid for and already reached
    # `query_log`. A spend bound exists to stop further spending, not to discard
    # the measurement that the spending bought.
    _write(records, total, plan, generator.identity, answer, complete=halted is None)
    if halted:
        raise SystemExit(halted)


def _write(
    records: list[dict[str, Any]],
    total: Decimal,
    plan: dict[str, Any],
    identity: str,
    answer: Any,
    *,
    complete: bool,
) -> None:
    RESULT.write_text(
        json.dumps(
            {
                "measured_at": datetime.now(UTC).date().isoformat(),
                "git_sha": subprocess.run(
                    ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
                ).stdout.strip(),
                "preregistration": "evals/prereg-refusal-nearmiss.md",
                "complete": complete,
                "generator_identity": identity,
                "prompt_version": answer.prompt_version if answer is not None else PROMPT_VERSION,
                "k": K,
                "query_plan": plan,
                "cost_usd": str(total),
                "records": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {RESULT.relative_to(REPO_ROOT)} — {len(records)} records, ${total}")


def main() -> None:
    load_env()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["verify", "recover", "run"])
    parser.add_argument("--only", default=None, help="verify a single question id")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag")
    if args.mode == "verify":
        asyncio.run(_verify(database_url, args.only))
    elif args.mode == "recover":
        asyncio.run(_recover(database_url))
    else:
        asyncio.run(_run(database_url))


if __name__ == "__main__":
    main()
