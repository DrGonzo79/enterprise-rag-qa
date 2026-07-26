# SPEC-002 — Data Model & Migrations

**Status:** Draft — awaiting architect approval
**Date:** 2026-07-25
**Depends on:** SPEC-001

## Purpose

Define the complete persistent schema — documents, chunks (dense + full-text indexed), query log, and evaluation records — as SQLAlchemy 2.0 typed ORM models with Alembic migrations, such that every later spec (ingestion, retrieval, eval harness) writes to a schema that already exists and is already tested.

## Non-goals

- Ingestion/chunking logic (later spec — this spec defines where chunks live, not how they're made)
- Retrieval queries, hybrid fusion, or ranking (later spec)
- Eval harness semantics (later spec — `eval_results.scores` is deliberately schema-light until then)
- Seeding or fixture data

## Interface

### Tables

**`documents`** — one row per ingested source file

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | pk |
| `source_uri` | text | not null |
| `title` | text | not null |
| `content_hash` | char(64) | **unique**, not null — sha256 of raw content; idempotent re-ingestion |
| `byte_size` | integer | not null |
| `created_at` | timestamptz | not null, default now |

**`chunks`** — retrieval unit

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | pk |
| `document_id` | uuid | fk → documents.id, **on delete cascade** |
| `ordinal` | integer | not null; **unique (document_id, ordinal)** |
| `text` | text | not null |
| `token_count` | integer | not null |
| `embedding` | vector(1536) | not null; **HNSW index** (`vector_cosine_ops`, m=16, ef_construction=64) |
| `embedding_model` | text | not null — records which model produced the vector |
| `tsv` | tsvector | **GENERATED ALWAYS AS** `to_tsvector('english', text)` **STORED**; **GIN index** |
| `created_at` | timestamptz | not null, default now |

**`query_log`** — one row per answered question

| Column | Type | Constraints |
|---|---|---|
| `id` | uuid | pk |
| `question` | text | not null |
| `provider` | text | not null |
| `model` | text | not null |
| `latency_ms` | integer | not null |
| `prompt_tokens` | integer | not null |
| `completion_tokens` | integer | not null |
| `cost_usd` | numeric(10,6) | not null — computed at query time (point-in-time pricing; token counts alone can't reconstruct it after price changes) |
| `retrieved_chunk_ids` | uuid[] | not null |
| `created_at` | timestamptz | not null, default now |

**`eval_runs`** / **`eval_results`**

| eval_runs | | eval_results | |
|---|---|---|---|
| `id` uuid pk | | `id` uuid pk | |
| `git_sha` text not null | | `run_id` uuid fk → eval_runs, cascade | |
| `dataset_name` text not null | | `case_id` text not null | |
| `config` jsonb not null | | `scores` jsonb not null | |
| `created_at` timestamptz | | `retrieved_chunk_ids` uuid[] not null | |
| | | `latency_ms` integer not null | |

### Code artifacts

- `src/rag_qa/db/models.py` — SQLAlchemy 2.0 `DeclarativeBase` with `Mapped[]` typing throughout
- `src/rag_qa/db/engine.py` — async engine (asyncpg) + session factory, `DATABASE_URL` from env
- `alembic/` — async template; **one migration per schema-touching spec**, starting with `0001` for everything above
- Dev dependency additions: sqlalchemy, asyncpg, alembic, pgvector (python package), tiktoken

## Key decisions

1. **Cosine distance** (`vector_cosine_ops`) — `text-embedding-3-small` outputs normalized vectors; cosine ≡ dot product, and cosine is the conventional, least-surprising choice.
2. **`tsv` as a stored generated column** — the database owns full-text sync; no application code can forget to update it.
3. **Document text lives in the DB** (chunk rows carry full text; no blob store) — corpus is tens of MB at most; a second storage system fails the same test the second database did.
4. **`token_count` uses tiktoken `cl100k_base`** — matches the embedding model's tokenizer, so counts are meaningful for embedding-window budgeting.
5. **Async end-to-end** (asyncpg + async SQLAlchemy + async Alembic template) — follows from pytest-asyncio in the decided stack; flagged as challenged decision #3 at review.
6. **`eval_results.scores` is jsonb** until the eval-harness spec exists; it will be tightened to typed columns in that spec (same-commit update rule).
7. **`embedding_model` column** — makes a future re-embedding detectable rather than silent (see challenged decision #1).

## Acceptance criteria

- **AC-1** `alembic upgrade head` on an empty database creates all five tables; `alembic downgrade base` returns the DB to empty. Both exit 0.
- **AC-2** After upgrade, `pg_indexes` contains the HNSW index on `chunks.embedding` and the GIN index on `chunks.tsv`.
- **AC-3** Inserting a second document with a duplicate `content_hash` raises an integrity error.
- **AC-4** Inserting a second chunk with a duplicate `(document_id, ordinal)` raises an integrity error.
- **AC-5** Inserting a chunk whose embedding is not 1536-dimensional fails.
- **AC-6** Inserting a chunk populates `tsv` without the application writing it, and a `@@ to_tsquery` match returns the row.
- **AC-7** Deleting a document cascades to its chunks.

## Test plan

`tests/test_data_model.py`, async, against the dockerized Postgres from SPEC-001 (CI: the service container):

- `test_migrations_roundtrip` (AC-1) — upgrade head → assert 5 tables via `information_schema` → downgrade base → assert 0
- `test_indexes_exist` (AC-2) — query `pg_indexes` for `ix_chunks_embedding_hnsw` and `ix_chunks_tsv_gin`
- `test_content_hash_unique` (AC-3), `test_chunk_ordinal_unique` (AC-4), `test_embedding_dimension_enforced` (AC-5) — each expects `IntegrityError`/`DataError`
- `test_tsv_generated_and_searchable` (AC-6)
- `test_document_delete_cascades` (AC-7)

Each test creates its rows inside a rolled-back transaction where possible; migration tests run against a dedicated scratch database.
