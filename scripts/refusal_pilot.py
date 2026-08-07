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
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.query_plan import EVAL_SERVER_SETTINGS, observed_vector_plan
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.db.models import SpendSource
from rag_qa.env import load_env
from rag_qa.generation.clients.anthropic import AnthropicClient
from rag_qa.generation.service import Generator
from rag_qa.ingest.embedder import OpenAIEmbeddingClient
from rag_qa.retrieval import Retriever

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = REPO_ROOT / "evals" / "refusal_pilot.jsonl"
RESULT = REPO_ROOT / "evals" / "refusal-pilot-result.json"
K = 8

# §4: the run stops rather than silently exceeding what was pre-registered.
COST_BOUND_USD = Decimal("0.40")


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

    records: list[dict[str, Any]] = []
    total = Decimal("0")
    try:
        async with factory() as session:
            plan = await observed_vector_plan(session)
        for case in cases:
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
                    "cited_section_paths": [
                        citation.section_path for citation in answer.citations
                    ],
                    "prompt_tokens": answer.prompt_tokens,
                    "completion_tokens": answer.completion_tokens,
                    "cost_usd": str(answer.cost_usd),
                }
            )
            print(f"{case['id']:>6} {case['arm']} {answer.verdict.value:<22} ${total}")
            if total > COST_BOUND_USD:
                raise SystemExit(f"cost {total} exceeded the pre-registered bound {COST_BOUND_USD}")
    finally:
        await engine.dispose()

    RESULT.write_text(
        json.dumps(
            {
                "measured_at": datetime.now(UTC).date().isoformat(),
                "git_sha": subprocess.run(
                    ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
                ).stdout.strip(),
                "preregistration": "evals/prereg-refusal-nearmiss.md",
                "generator_identity": generator.identity,
                "prompt_version": answer.prompt_version,
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
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["verify", "run"])
    parser.add_argument("--only", default=None, help="verify a single question id")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag")
    if args.mode == "verify":
        asyncio.run(_verify(database_url, args.only))
    else:
        asyncio.run(_run(database_url))


if __name__ == "__main__":
    main()
