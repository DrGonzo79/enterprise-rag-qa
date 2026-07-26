"""Real-corpus retrieval quality and latency (SPEC-004 AC-6, AC-8 local tier).

Skipped in CI: requires the ingested corpus and a real OPENAI_API_KEY for
query embeddings (EUR-Lex's WAF makes networked CI ingestion
non-deterministic — SPEC-003 test plan).

**Measuring always runs; writing the baseline artifact requires
`--write-baseline` (SPEC-004 AC-13).** The assertions below are the test; the
artifact is a record of a corpus state that cannot be reconstructed once the
corpus changes, so it is never produced as a side effect of `pytest`. Without
the flag the measured table is printed and nothing on disk moves.
"""

import json
import os
import statistics
import time
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rag_qa.env import load_env
from rag_qa.retrieval import Retriever
from rag_qa.retrieval.metrics import distinct_section_rate
from rag_qa.retrieval.search import vector_search
from rag_qa.retrieval.types import RetrievedChunk

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SET = REPO_ROOT / "evals" / "retrieval_smoke.jsonl"
BASELINE = REPO_ROOT / "evals" / "retrieval_baseline.json"
K = 8

load_env()
CORPUS_DATABASE_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)
HAS_API_KEY = bool(os.environ.get("OPENAI_API_KEY"))

pytestmark = pytest.mark.skipif(
    not HAS_API_KEY, reason="real-corpus tier needs OPENAI_API_KEY (skipped in CI)"
)


def load_cases() -> list[dict[str, str]]:
    return [json.loads(line) for line in SMOKE_SET.read_text(encoding="utf-8").splitlines() if line]


def hit(chunks: list[RetrievedChunk], prefix: str) -> int | None:
    """1-based rank of the first chunk whose section_path matches, else None."""
    for rank, chunk in enumerate(chunks, start=1):
        if chunk.section_path.startswith(prefix):
            return rank
    return None


def recall_at(ranks: list[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr_at_k(ranks: list[int | None]) -> float:
    return sum(1 / r for r in ranks if r is not None) / len(ranks) if ranks else 0.0


@pytest.fixture(scope="session")
async def corpus_retriever():  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from rag_qa.ingest.embedder import OpenAIEmbeddingClient

    engine = create_async_engine(CORPUS_DATABASE_URL, pool_size=4, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        chunk_count = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    if not chunk_count:
        await engine.dispose()
        pytest.skip(f"no ingested corpus at {CORPUS_DATABASE_URL}")

    yield Retriever(factory, OpenAIEmbeddingClient()), factory
    await engine.dispose()


async def test_hybrid_beats_vector_only_and_records_the_baseline(
    corpus_retriever, write_baseline: bool
) -> None:  # type: ignore[no-untyped-def]
    retriever, factory = corpus_retriever
    cases = load_cases()
    assert len(cases) >= 24
    assert sum(1 for c in cases if c["style"] == "citation") >= 10
    assert sum(1 for c in cases if c["style"] == "paraphrase") >= 10

    rows: list[dict[str, object]] = []
    latencies: list[float] = []
    diversity: list[float] = []

    for case in cases:
        started = time.perf_counter()
        hybrid = await retriever.retrieve(case["question"], k=K)
        latencies.append((time.perf_counter() - started) * 1000)
        diversity.append(distinct_section_rate(hybrid))

        # Vector-only comparison reuses the SAME query embedding, so the only
        # difference is the absence of the full-text branch.
        query_vector = (await retriever._query_embedder.embed([case["question"]]))[0]
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
                "style": case["style"],
                "question": case["question"],
                "expected": case["expected_section_prefix"],
                "hybrid_rank": hit(hybrid, case["expected_section_prefix"]),
                "vector_rank": hit(vector_only, case["expected_section_prefix"]),
                "distinct_section_rate": distinct_section_rate(hybrid),
            }
        )

    def subset(style: str | None) -> list[dict[str, object]]:
        return [r for r in rows if style is None or r["style"] == style]

    def ranks(items: list[dict[str, object]], key: str) -> list[int | None]:
        return [r[key] for r in items]  # type: ignore[misc]

    def recall_table() -> dict[str, dict[str, float]]:
        table: dict[str, dict[str, float]] = {}
        for style, label in (
            (None, "overall"),
            ("citation", "citation"),
            ("paraphrase", "paraphrase"),
        ):
            items = subset(style)
            for at in (1, 3, K):
                table.setdefault(f"recall_at_{at}", {})[f"hybrid_{label}"] = round(
                    recall_at(ranks(items, "hybrid_rank"), at), 4
                )
                table[f"recall_at_{at}"][f"vector_only_{label}"] = round(
                    recall_at(ranks(items, "vector_rank"), at), 4
                )
        return table

    measured = recall_table()
    citation_hybrid_1 = measured["recall_at_1"]["hybrid_citation"]
    citation_vector_1 = measured["recall_at_1"]["vector_only_citation"]
    overall_hybrid_k = measured[f"recall_at_{K}"]["hybrid_overall"]
    overall_vector_k = measured[f"recall_at_{K}"]["vector_only_overall"]

    baseline = {
        "note": (
            "Measured baseline, not a threshold. SPEC-004 AC-6 sets no floor; SPEC-007 sets "
            "one against the 50-question golden set. recall@3 and recall@8 saturate at 1.000 "
            "for both methods on this 358-chunk corpus — k=1 is the only regime with "
            "discriminating power. See SPEC-004 Key decision 12 for the unresolved finding "
            "that plain RRF loses to vector-only on paraphrase queries."
        ),
        "measured_on": "358-chunk corpus (NIST AI RMF, EU AI Act, NVDA 10-K)",
        "k": K,
        "cases": len(rows),
        **measured,
        "mrr_at_k": {
            "hybrid_overall": round(mrr_at_k(ranks(subset(None), "hybrid_rank")), 4),
            "vector_only_overall": round(mrr_at_k(ranks(subset(None), "vector_rank")), 4),
            "hybrid_citation": round(mrr_at_k(ranks(subset("citation"), "hybrid_rank")), 4),
            "vector_only_citation": round(mrr_at_k(ranks(subset("citation"), "vector_rank")), 4),
            "hybrid_paraphrase": round(mrr_at_k(ranks(subset("paraphrase"), "hybrid_rank")), 4),
            "vector_only_paraphrase": round(
                mrr_at_k(ranks(subset("paraphrase"), "vector_rank")), 4
            ),
        },
        "distinct_section_rate": {
            "mean": round(statistics.fmean(diversity), 4),
            "median": round(statistics.median(diversity), 4),
            "min": round(min(diversity), 4),
            "max": round(max(diversity), 4),
        },
        "latency_ms": {
            "end_to_end_p50": round(statistics.median(latencies), 1),
            "end_to_end_p95": round(statistics.quantiles(latencies, n=20)[-1], 1),
            "end_to_end_max": round(max(latencies), 1),
        },
        "per_case": rows,
    }
    if write_baseline:
        BASELINE.write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    else:
        print(f"\n[baseline not written — pass --write-baseline to record {BASELINE.name}]")

    print(f"\n{'id':<8}{'style':<12}{'hybrid':>8}{'vector':>8}  expected")
    for row in rows:
        print(
            f"{row['id']:<8}{row['style']:<12}{row['hybrid_rank']!s:>8}"
            f"{row['vector_rank']!s:>8}  {row['expected']}"
        )
    for at in (1, 3, K):
        print(f"recall@{at}: {json.dumps(measured[f'recall_at_{at}'])}")
    print(f"mrr@{K}: {json.dumps(baseline['mrr_at_k'])}")
    print(f"diversity: {json.dumps(baseline['distinct_section_rate'])}")

    # AC-6, asserted where the measurement discriminates (Key decision 10 +
    # Key decision 12): at k=1 hybrid must beat vector-only on citation-style
    # queries — the claim hybrid retrieval rests on.
    assert citation_hybrid_1 > citation_vector_1, (
        f"hybrid recall@1 on citation-style queries ({citation_hybrid_1:.3f}) did not beat "
        f"vector-only ({citation_vector_1:.3f}) — the claim hybrid retrieval rests on"
    )
    # Satisfied but VACUOUS at this corpus size (both 1.000): kept because the
    # approved AC names it, recorded as vacuous so it is never cited as evidence.
    assert overall_hybrid_k >= overall_vector_k, (
        f"hybrid recall@{K} {overall_hybrid_k:.3f} regressed against vector-only "
        f"{overall_vector_k:.3f}"
    )
    # The paraphrase regression (Key decision 12) is deliberately RECORDED, not
    # asserted: fixing it means changing the fusion rule, which amends approved
    # decisions on the strength of 26 questions. SPEC-007 decides with data.


async def test_latency_against_the_real_corpus(
    corpus_retriever,  # type: ignore[no-untyped-def]
    caplog: pytest.LogCaptureFixture,
    write_baseline: bool,
) -> None:
    """AC-8 local tier. The end-to-end p95 is dominated by the OpenAI embedding
    round-trip (measured p95 843ms against 16ms of retrieval work), so the tight
    assertion goes on the budget this code owns and the end-to-end distribution
    is recorded. See SPEC-004 AC-8."""
    import logging

    retriever, _ = corpus_retriever
    cases = load_cases()

    await retriever.retrieve(cases[0]["question"], k=K)  # warm pool + index

    with caplog.at_level(logging.INFO, logger="rag_qa.retrieval.service"):
        caplog.clear()
        for case in cases:
            await retriever.retrieve(case["question"], k=K)
        records = [r for r in caplog.records if r.name == "rag_qa.retrieval.service"]

    assert len(records) == len(cases)
    end_to_end = [r.total_ms for r in records]  # type: ignore[attr-defined]
    embed = [r.embed_ms for r in records]  # type: ignore[attr-defined]
    # Branches run concurrently, so retrieval-side cost is the slower branch.
    retrieval_side = [
        max(r.vector_ms, r.fts_ms) + r.fuse_ms  # type: ignore[attr-defined]
        for r in records
    ]

    def p95(values: list[float]) -> float:
        return statistics.quantiles(values, n=20)[-1]

    stage_split = {
        "embed_ms": {"p50": round(statistics.median(embed), 1), "p95": round(p95(embed), 1)},
        "retrieval_side_ms": {
            "p50": round(statistics.median(retrieval_side), 1),
            "p95": round(p95(retrieval_side), 1),
        },
        "end_to_end_ms": {
            "p50": round(statistics.median(end_to_end), 1),
            "p95": round(p95(end_to_end), 1),
            "max": round(max(end_to_end), 1),
        },
    }
    print(f"\nstage split: {json.dumps(stage_split, indent=2)}")

    if write_baseline and BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        baseline["stage_latency_split_ms"] = stage_split
        BASELINE.write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    assert p95(retrieval_side) <= 150, (
        f"retrieval-side p95 {p95(retrieval_side):.0f}ms exceeds the 150ms budget this code owns "
        f"(embedding round-trip excluded; embed p95 was {p95(embed):.0f}ms)"
    )
    assert statistics.median(end_to_end) <= 800, (
        f"end-to-end p50 {statistics.median(end_to_end):.0f}ms exceeds the 800ms target "
        f"(embed p50 {statistics.median(embed):.0f}ms)"
    )
