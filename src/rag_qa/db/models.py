"""SQLAlchemy 2.0 typed ORM models for the SPEC-002 schema."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1536


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # semantic category for retrieval filters (SPEC-004): standard | regulation | filing
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)
    # sha256 over raw content ‖ chunking config (SPEC-002 Key decision 9)
    content_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        # No index on `embedding`. The HNSW index that stood here was never
        # reachable — `vector_stmt` orders by `(distance, id)` and an HNSW index
        # can only order by the distance operator alone — and dense search is
        # exact at this corpus size (16 ms p95 against a 150 ms budget). Dropped
        # with the full-text branch under SPEC-004 KD-15; the trigger for
        # reinstating it is AC-8's budget, not corpus size on its own.
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_id_ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # breadcrumb like "EU AI Act › Chapter III › Article 6" (SPEC-003 decision 4)
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class SpendSource(StrEnum):
    """Who spent the money on a `query_log` row (SPEC-002 migration 0005).

    The discriminator the two ceilings need. SPEC-006 Key decision 16 amendment 7
    scopes the **daily** window to `visitor` — its job is shaping visitor burst,
    and an evaluation run must not close the demo for the rest of the day — while
    the **monthly** cap counts every source, because the monthly figure is the
    invoice and the invoice includes every call this project makes.

    `VISITOR` is the default everywhere on purpose: it is the value that presses
    *both* ceilings, so a writer that forgets to tag itself is treated as the
    most constrained kind of traffic rather than the least.
    """

    VISITOR = "visitor"
    EVAL = "eval"
    CLI = "cli"


class QueryLog(Base):
    __tablename__ = "query_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Any] = mapped_column(Numeric(10, 6), nullable=False)
    retrieved_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    # SPEC-005 migration 0004. verdict: refusal is a scored capability and must
    # not be recovered by string-matching answer text. answer_text: without it the
    # log records numbers about text nobody kept. prompt_version: attributes a
    # logged answer to the prompt that produced it.
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    # SPEC-002 migration 0005. Which ceiling this row presses -- see SpendSource.
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=SpendSource.VISITOR.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    git_sha: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_name: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(Text, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retrieved_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
