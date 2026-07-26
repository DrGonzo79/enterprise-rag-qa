"""Ingestion pipeline: route -> load -> hash -> skip/replace -> chunk ->
embed -> upsert, plus the ingestion manifest (SPEC-003 Interface).

Embedding happens outside the DB transaction (Key decision 7); the upsert is
one transaction: delete-by-source_uri (cascade) + insert document and chunks.
"""

import json
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_qa.db.models import Chunk, Document
from rag_qa.ingest.chunker import chunk_document
from rag_qa.ingest.embedder import EmbeddingClient, embed_all
from rag_qa.ingest.loaders import load_edgar_10k, load_eurlex_html, load_nist_pdf
from rag_qa.ingest.types import (
    ChunkDraft,
    IngestConfig,
    ParsedDocument,
    compute_content_hash,
)

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "ingest-manifest.json"
# text-embedding-3-small pricing, USD per 1M tokens
EMBEDDING_USD_PER_MTOK = 0.02

SessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class DocumentReport:
    document: str
    verdict: str  # "new" | "unchanged" | "replace" | "dry-run"
    content_hash: str
    sections: int
    chunks: int
    tokens: int
    estimated_embedding_usd: float


@dataclass
class Manifest:
    config: dict[str, object]
    documents: list[DocumentReport] = field(default_factory=list[DocumentReport])
    dropped_tables: list[dict[str, object]] = field(default_factory=list[dict[str, object]])

    def write(self, directory: Path) -> Path:
        path = directory / MANIFEST_FILENAME
        payload = {
            "config": self.config,
            "documents": [asdict(d) for d in self.documents],
            "dropped_tables": self.dropped_tables,
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


def route_loader(path: Path, config: IngestConfig) -> Callable[[Path], ParsedDocument] | None:
    """Pick a loader by suffix + content sniff (SPEC-003 Interface)."""
    if path.suffix.lower() == ".pdf":
        return load_nist_pdf
    if path.suffix.lower() in (".html", ".htm"):
        head = path.read_bytes()[:65536]
        if b"eli-subdivision" in head:
            return load_eurlex_html
        if b"ix:" in head or b"xbrl" in head.lower():
            return lambda p: load_edgar_10k(p, config)
    return None


def discover(directory: Path, config: IngestConfig) -> list[Path]:
    return sorted(
        p for p in directory.iterdir() if p.is_file() and route_loader(p, config) is not None
    )


async def _existing_hashes(session: AsyncSession) -> set[str]:
    rows = await session.execute(select(Document.content_hash))
    return {row[0] for row in rows}


async def _upsert(
    session: AsyncSession,
    doc: ParsedDocument,
    drafts: list[ChunkDraft],
    vectors: list[list[float]],
    content_hash: str,
    embedder_identity: str,
) -> str:
    existing = (
        (await session.execute(select(Document).where(Document.source_uri == doc.source_uri)))
        .scalars()
        .all()
    )
    verdict = "replace" if existing else "new"
    for old in existing:
        await session.delete(old)
    await session.flush()

    document = Document(
        id=uuid.uuid4(),
        source_uri=doc.source_uri,
        title=doc.title,
        doc_type=doc.doc_type,
        content_hash=content_hash,
        byte_size=len(doc.raw_bytes),
    )
    session.add(document)
    await session.flush()
    for ordinal, (draft, vector) in enumerate(zip(drafts, vectors, strict=True)):
        session.add(
            Chunk(
                id=uuid.uuid4(),
                document_id=document.id,
                ordinal=ordinal,
                text=draft.text,
                token_count=draft.token_count,
                section_path=draft.section_path,
                embedding=vector,
                # the client's identity, never a constant: a fake-embedder run
                # must be distinguishable in the DB (SPEC-004 KD-4)
                embedding_model=embedder_identity,
            )
        )
    await session.commit()
    return verdict


async def ingest_paths(
    paths: list[Path],
    config: IngestConfig,
    *,
    dry_run: bool,
    session_provider: SessionProvider | None = None,
    embedding_client: EmbeddingClient | None = None,
    manifest_dir: Path | None = None,
) -> Manifest:
    manifest = Manifest(config=json.loads(config.canonical_json()))

    for path in paths:
        loader = route_loader(path, config)
        if loader is None:
            logger.warning("no loader recognizes %s; skipping", path.name)
            continue

        content_hash = compute_content_hash(path.read_bytes(), config)

        if not dry_run:
            assert session_provider is not None and embedding_client is not None
            async with session_provider() as session:
                if content_hash in await _existing_hashes(session):
                    logger.info("%s unchanged (hash match); skipping", path.name)
                    manifest.documents.append(
                        DocumentReport(
                            document=path.name,
                            verdict="unchanged",
                            content_hash=content_hash,
                            sections=0,
                            chunks=0,
                            tokens=0,
                            estimated_embedding_usd=0.0,
                        )
                    )
                    continue

        doc = loader(path)
        drafts = chunk_document(doc, config)
        total_tokens = sum(d.token_count for d in drafts)
        cost = total_tokens / 1_000_000 * EMBEDDING_USD_PER_MTOK
        manifest.dropped_tables.extend(asdict(t) for t in doc.dropped_tables)

        if dry_run:
            manifest.documents.append(
                DocumentReport(
                    document=path.name,
                    verdict="dry-run",
                    content_hash=content_hash,
                    sections=len(doc.sections),
                    chunks=len(drafts),
                    tokens=total_tokens,
                    estimated_embedding_usd=round(cost, 6),
                )
            )
            continue

        assert session_provider is not None and embedding_client is not None
        vectors = await embed_all([d.text for d in drafts], embedding_client)
        async with session_provider() as session:
            verdict = await _upsert(
                session, doc, drafts, vectors, content_hash, embedding_client.identity
            )
        manifest.documents.append(
            DocumentReport(
                document=path.name,
                verdict=verdict,
                content_hash=content_hash,
                sections=len(doc.sections),
                chunks=len(drafts),
                tokens=total_tokens,
                estimated_embedding_usd=round(cost, 6),
            )
        )

    if manifest_dir is not None:
        written = manifest.write(manifest_dir)
        logger.info("manifest written to %s", written)
    return manifest
