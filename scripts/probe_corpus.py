"""Pre-flight probes for a candidate rung (SPEC-003 AC-12, extended).

Two probes, and the second is the one that decides anything.

**Format probe** — does the document parse under its unmodified loader, into a
plausible section hierarchy? A candidate that fails is dropped from the set
rather than accommodated (SPEC-003 Key decision 16).

**Competition probe** — does the document *compete*? Rung 0 measured `neither = 0`:
every question answered by both methods, no gold chunk ever displaced. `recall@8`
only falls when eight chunks outrank the gold chunk, and discordance only appears
near a ranking boundary. **Neither is produced by volume.** A topically disjoint
document adds chunks that never rank, costs money to embed, and moves no number —
so the probe reports, per candidate:

- how many of its chunks reach the top 8 for the existing questions,
- how many *displace* a gold chunk out of the top 8,
- how far it pushes the gold chunk down when it does not displace it,
- and how all of that differs between hybrid and vector-only, since a document
  that competes on one arm and not the other is what creates discordant pairs.

**Nothing here touches the corpus database.** Candidates are loaded into a
scratch database seeded with a copy of the real corpus, measured, and removed.
The scratch database is dropped at the end unless `--keep`.

Usage:
    uv run python -m scripts.probe_corpus --rung rung-1
"""

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from rag_qa.db.models import Chunk, Document
from rag_qa.env import load_env
from rag_qa.ingest.chunker import chunk_document
from rag_qa.ingest.embedder import OpenAIEmbeddingClient, embed_all
from rag_qa.ingest.loaders import load_edgar_10k, load_eurlex_html, load_nist_pdf
from rag_qa.ingest.pipeline import EMBEDDING_USD_PER_MTOK
from rag_qa.ingest.registry import RegisteredDocument, for_rung, load
from rag_qa.ingest.types import IngestConfig, ParsedDocument
from rag_qa.retrieval.search import vector_search
from rag_qa.retrieval.service import Retriever
from scripts.section_match import matches_section

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "corpus" / "corpus.toml"
CORPUS_DIR = REPO_ROOT / "corpus"
SMOKE_SET = REPO_ROOT / "evals" / "retrieval_smoke.jsonl"
OUT_DIR = REPO_ROOT / "evals" / "probes"
K = 8

load_env()
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)
PROBE_DB = "rag_probe"
PROBE_URL = CORPUS_URL.rsplit("/", 1)[0] + f"/{PROBE_DB}"
ADMIN_URL = CORPUS_URL.replace("+asyncpg", "").rsplit("/", 1)[0] + "/postgres"


# --- loading ------------------------------------------------------------------


def parse_with_registry_metadata(document: RegisteredDocument, path: Path) -> ParsedDocument:
    """Load, then stamp the registry's identity over the loader's constants.

    `nist_pdf` and `eurlex_html` hardcode the title, source URI and — critically —
    the `DOC_LABEL` that becomes the first element of every `heading_path` and
    therefore of every `section_path`. Left alone, GDPR's chunks would enter the
    measurement announcing themselves as "EU AI Act › …", which is not merely
    mislabelled: the gold answers are matched by `section_path` prefix, so a
    candidate chunk could be counted as a *gold hit* for a document it is not.
    """
    if document.loader == "nist_pdf":
        parsed = load_nist_pdf(path)
    elif document.loader == "eurlex_html":
        parsed = load_eurlex_html(path)
    else:
        parsed = load_edgar_10k(path, IngestConfig())

    original_label = parsed.sections[0].heading_path[0] if parsed.sections else ""
    sections = tuple(
        replace(section, heading_path=(document.doc_label, *section.heading_path[1:]))
        if section.heading_path and section.heading_path[0] == original_label
        else section
        for section in parsed.sections
    )
    return replace(
        parsed,
        source_uri=document.source_uri,
        title=document.title,
        doc_type=document.doc_type,
        sections=sections,
    )


# --- scratch database ---------------------------------------------------------


async def _admin(sql: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


async def create_probe_database() -> None:
    import asyncpg

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        exists = await connection.fetchrow("SELECT 1 FROM pg_database WHERE datname = $1", PROBE_DB)
        if exists:
            await connection.execute(f'DROP DATABASE "{PROBE_DB}" WITH (FORCE)')
        await connection.execute(f'CREATE DATABASE "{PROBE_DB}"')
    finally:
        await connection.close()

    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", PROBE_URL)
    engine = create_async_engine(PROBE_URL)
    async with engine.connect() as conn:

        def upgrade(sync_conn: object) -> None:
            config.attributes["connection"] = sync_conn
            command.upgrade(config, "head")

        await conn.run_sync(upgrade)
        await conn.commit()
    await engine.dispose()


async def copy_corpus_into_probe() -> int:
    """Copy the real corpus into the scratch database, read-only at the source."""
    source = create_async_engine(CORPUS_URL)
    target = create_async_engine(PROBE_URL)
    # Columns are named rather than `SELECT *`: `chunks.tsv` is generated and
    # cannot be inserted, and a copy that silently picks up a new column later
    # would fail in the middle of a probe rather than at its start.
    doc_cols = "id, source_uri, title, doc_type, content_hash, byte_size, created_at"
    chunk_cols = (
        "id, document_id, ordinal, text, token_count, section_path, embedding, embedding_model"
    )
    async with source.connect() as src:
        documents = (await src.execute(text(f"SELECT {doc_cols} FROM documents"))).mappings().all()
        chunks = (await src.execute(text(f"SELECT {chunk_cols} FROM chunks"))).mappings().all()
    async with target.begin() as dst:
        for row in documents:
            await dst.execute(
                text(
                    f"INSERT INTO documents ({doc_cols}) VALUES (:id, :source_uri, :title, "
                    ":doc_type, :content_hash, :byte_size, :created_at)"
                ),
                dict(row),
            )
        for row in chunks:
            await dst.execute(
                text(
                    f"INSERT INTO chunks ({chunk_cols}) VALUES (:id, :document_id, :ordinal, "
                    ":text, :token_count, :section_path, :embedding, :embedding_model)"
                ),
                dict(row),
            )
    await source.dispose()
    await target.dispose()
    return len(chunks)


# --- measurement --------------------------------------------------------------


@dataclass
class ArmResult:
    """One retrieval arm (hybrid or vector-only) over the whole question set."""

    gold_ranks: dict[str, int | None] = field(default_factory=dict)
    candidate_in_top8: dict[str, int] = field(default_factory=dict)

    @property
    def recall_at_8(self) -> float:
        hits = sum(1 for r in self.gold_ranks.values() if r is not None and r <= K)
        return hits / len(self.gold_ranks) if self.gold_ranks else 0.0

    @property
    def questions_with_candidate(self) -> int:
        return sum(1 for n in self.candidate_in_top8.values() if n)

    @property
    def candidate_appearances(self) -> int:
        return sum(self.candidate_in_top8.values())


def _gold_rank(chunks: list[object], prefix: str) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        if matches_section(prefix, chunk.section_path):  # type: ignore[attr-defined]
            return rank
    return None


async def measure(
    factory: async_sessionmaker,  # type: ignore[type-arg]
    retriever: Retriever,
    cases: list[dict[str, str]],
    candidate_doc_ids: set[uuid.UUID],
) -> tuple[ArmResult, ArmResult]:
    hybrid_arm, vector_arm = ArmResult(), ArmResult()
    for case in cases:
        cid = case["id"]
        prefix = case["expected_section_prefix"]

        hybrid = await retriever.retrieve(case["question"], k=K)
        hybrid_arm.gold_ranks[cid] = _gold_rank(hybrid, prefix)
        hybrid_arm.candidate_in_top8[cid] = sum(
            1 for c in hybrid if c.document_id in candidate_doc_ids
        )

        query_vector = (await retriever._query_embedder.embed([case["question"]]))[0]
        async with factory() as session:
            dense = await vector_search(session, query_vector)
        top = dense[:K]
        vector_arm.gold_ranks[cid] = _gold_rank(top, prefix)  # type: ignore[arg-type]
        vector_arm.candidate_in_top8[cid] = sum(
            1 for row in top if row.document_id in candidate_doc_ids
        )
    return hybrid_arm, vector_arm


async def insert_candidate(
    factory: async_sessionmaker,  # type: ignore[type-arg]
    parsed: ParsedDocument,
    config: IngestConfig,
    embedder: OpenAIEmbeddingClient,
    cache: dict[str, list[list[float]]] | None = None,
) -> tuple[uuid.UUID, int, int]:
    """Vectors are cached by source_uri because every candidate is inserted twice
    — once alone, once in the combined run — and embedding it twice is a real
    charge for an identical result. The first run of this probe paid $0.0176 for
    $0.0088 of work."""
    drafts = chunk_document(parsed, config)
    cached = cache.get(parsed.source_uri) if cache is not None else None
    vectors = cached if cached is not None else await embed_all([d.text for d in drafts], embedder)
    if cache is not None:
        cache[parsed.source_uri] = vectors
    document_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Document(
                id=document_id,
                source_uri=parsed.source_uri,
                title=parsed.title,
                doc_type=parsed.doc_type,
                content_hash=uuid.uuid4().hex,
                byte_size=len(parsed.raw_bytes),
            )
        )
        await session.flush()
        for ordinal, (draft, vector) in enumerate(zip(drafts, vectors, strict=True)):
            session.add(
                Chunk(
                    id=uuid.uuid4(),
                    document_id=document_id,
                    ordinal=ordinal,
                    text=draft.text,
                    token_count=draft.token_count,
                    section_path=draft.section_path,
                    embedding=vector,
                    embedding_model=embedder.identity,
                )
            )
        await session.commit()
    return document_id, len(drafts), sum(d.token_count for d in drafts)


async def remove_documents(
    factory: async_sessionmaker,  # type: ignore[type-arg]
    document_ids: list[uuid.UUID],
) -> None:
    async with factory() as session:
        for document_id in document_ids:
            await session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
        await session.commit()


# --- orchestration ------------------------------------------------------------


def load_cases() -> list[dict[str, str]]:
    return [json.loads(line) for line in SMOKE_SET.read_text(encoding="utf-8").splitlines() if line]


def format_probe(document: RegisteredDocument, parsed: ParsedDocument, chunks: int) -> dict:
    depths = [len(s.heading_path) for s in parsed.sections]
    return {
        "id": document.id,
        "sections": len(parsed.sections),
        "chunks": chunks,
        "estimated_chunks": document.estimated_chunks,
        "max_heading_depth": max(depths) if depths else 0,
        # A NIST PDF with no outline collapses to a flat list of top-level
        # sections; that is the failure Key decision 16 drops a document for.
        "hierarchy_ok": (max(depths) if depths else 0) >= 2,
        "sample_sections": [" › ".join(s.heading_path) for s in parsed.sections[:3]],
    }


async def run(rung: str, keep: bool) -> int:
    documents = for_rung(load(REGISTRY), rung)
    if not documents:
        print(f"no documents registered for {rung}", file=sys.stderr)
        return 2
    missing = [d.id for d in documents if not (CORPUS_DIR / d.filename).exists()]
    if missing:
        print(f"not fetched: {', '.join(missing)} — run fetch_corpus first", file=sys.stderr)
        return 2

    cases = load_cases()
    config = IngestConfig()
    embedder = OpenAIEmbeddingClient()

    print(f"building scratch database {PROBE_DB} (the corpus database is never written)")
    await create_probe_database()
    copied = await copy_corpus_into_probe()
    print(f"copied {copied} chunks from the corpus\n")

    engine: AsyncEngine = create_async_engine(PROBE_URL, pool_size=4, max_overflow=2)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    retriever = Retriever(factory, embedder)

    base_hybrid, base_vector = await measure(factory, retriever, cases, set())
    print(
        f"baseline: hybrid recall@8 {base_hybrid.recall_at_8:.3f}, "
        f"vector-only {base_vector.recall_at_8:.3f}\n"
    )

    results: list[dict] = []
    total_tokens = 0
    parsed_by_id: dict[str, ParsedDocument] = {}
    vector_cache: dict[str, list[list[float]]] = {}

    for document in documents:
        parsed = parse_with_registry_metadata(document, CORPUS_DIR / document.filename)
        parsed_by_id[document.id] = parsed
        document_id, chunks, tokens = await insert_candidate(
            factory, parsed, config, embedder, vector_cache
        )
        total_tokens += tokens
        hybrid, vector = await measure(factory, retriever, cases, {document_id})

        entry = {
            **format_probe(document, parsed, chunks),
            "tokens": tokens,
            "hybrid": _arm_summary(base_hybrid, hybrid),
            "vector_only": _arm_summary(base_vector, vector),
            # The ranking criterion Rung 0 argues for: competition per chunk, not
            # chunks. A document that adds 4,000 chunks and no top-8 appearances
            # costs money and moves no number, and it looks identical to a good
            # candidate on any size-based measure.
            "competition_density": round(hybrid.candidate_appearances / max(chunks, 1), 4),
        }
        results.append(entry)
        print(
            f"{document.id:<24} chunks {chunks:>4}  "
            f"top8 hybrid {entry['hybrid']['appearances']:>3} "
            f"(q {entry['hybrid']['questions_touched']:>2})  "
            f"vector {entry['vector_only']['appearances']:>3} "
            f"(q {entry['vector_only']['questions_touched']:>2})  "
            f"displaced h/v {entry['hybrid']['gold_displaced']}/"
            f"{entry['vector_only']['gold_displaced']}"
        )
        await remove_documents(factory, [document_id])

    # All candidates together: the closest thing to a Rung 1 preview that can be
    # had without ingesting anything.
    combined_ids: list[uuid.UUID] = []
    for document in documents:
        document_id, _, _ = await insert_candidate(
            factory, parsed_by_id[document.id], config, embedder, vector_cache
        )
        combined_ids.append(document_id)
    hybrid_all, vector_all = await measure(factory, retriever, cases, set(combined_ids))
    discordant = _discordance(hybrid_all, vector_all)
    combined = {
        "chunks_added": sum(r["chunks"] for r in results),
        "hybrid": _arm_summary(base_hybrid, hybrid_all),
        "vector_only": _arm_summary(base_vector, vector_all),
        **discordant,
    }
    await remove_documents(factory, combined_ids)
    await engine.dispose()

    if not keep:
        await _admin(f'DROP DATABASE "{PROBE_DB}" WITH (FORCE)')

    payload = {
        "rung": rung,
        "questions": len(cases),
        "k": K,
        "embedder_identity": embedder.identity,
        "baseline": {
            "hybrid_recall_at_8": round(base_hybrid.recall_at_8, 4),
            "vector_only_recall_at_8": round(base_vector.recall_at_8, 4),
        },
        "candidates": results,
        "all_candidates_together": combined,
        "embedding_tokens": total_tokens,
        "embedding_usd": round(total_tokens / 1_000_000 * EMBEDDING_USD_PER_MTOK, 6),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"probe-{rung}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nall {len(documents)} together: {combined['chunks_added']} chunks added")
    print(
        f"  hybrid recall@8 {base_hybrid.recall_at_8:.3f} -> "
        f"{combined['hybrid']['recall_at_8']:.3f}   "
        f"vector-only {base_vector.recall_at_8:.3f} -> "
        f"{combined['vector_only']['recall_at_8']:.3f}"
    )
    print(f"  discordant pairs: {discordant['n_discordant']} of {len(cases)}")
    print(f"\nembedding spend: {total_tokens:,} tokens, ${payload['embedding_usd']}")
    print(f"written: {out.relative_to(REPO_ROOT)}")
    return 0


def _arm_summary(base: ArmResult, after: ArmResult) -> dict:
    displaced = [
        cid
        for cid, rank in after.gold_ranks.items()
        if (base.gold_ranks[cid] is not None and base.gold_ranks[cid] <= K)
        and (rank is None or rank > K)
    ]
    pushed = {
        cid: after.gold_ranks[cid] - base.gold_ranks[cid]  # type: ignore[operator]
        for cid in after.gold_ranks
        if base.gold_ranks[cid] is not None
        and after.gold_ranks[cid] is not None
        and after.gold_ranks[cid] != base.gold_ranks[cid]  # type: ignore[operator]
    }
    return {
        "recall_at_8": round(after.recall_at_8, 4),
        "appearances": after.candidate_appearances,
        "questions_touched": after.questions_with_candidate,
        "gold_displaced": len(displaced),
        "gold_displaced_ids": displaced,
        "gold_rank_moved": len(pushed),
        "mean_gold_rank_change": round(sum(pushed.values()) / len(pushed), 2) if pushed else 0.0,
    }


def _discordance(hybrid: ArmResult, vector: ArmResult) -> dict:
    def ok(arm: ArmResult, cid: str) -> bool:
        rank = arm.gold_ranks[cid]
        return rank is not None and rank <= K

    ids = list(hybrid.gold_ranks)
    b = sum(1 for cid in ids if ok(hybrid, cid) and not ok(vector, cid))
    c = sum(1 for cid in ids if ok(vector, cid) and not ok(hybrid, cid))
    return {
        "hybrid_only": b,
        "vector_only_wins": c,
        "n_discordant": b + c,
        "both": sum(1 for cid in ids if ok(hybrid, cid) and ok(vector, cid)),
        "neither": sum(1 for cid in ids if not ok(hybrid, cid) and not ok(vector, cid)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rung", default="rung-1")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.rung, args.keep))


if __name__ == "__main__":
    sys.exit(main())
