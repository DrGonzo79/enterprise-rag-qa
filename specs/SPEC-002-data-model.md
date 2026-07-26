# SPEC-002 — Data Model & Migrations

**Status:** Approved — 2026-07-25
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
| `content_hash` | char(64) | **unique**, not null — sha256 over raw content **plus the chunking configuration** (strategy, target size, overlap); idempotent re-ingestion keyed on both (Key decision 9) |
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
5. **Async data layer, single implementation** — resolved 2026-07-25 after explicit async-vs-sync review; supersedes the earlier "flagged as challenged decision #3" note.
   - **Request path is async**: LLM generation is multi-second streamed I/O; async lets one worker hold many in-flight requests, streaming via `StreamingResponse` over async generators is the native FastAPI idiom, and hybrid retrieval runs the vector and tsvector queries concurrently before RRF fusion. CI is already wired for asyncpg (`DATABASE_URL=postgresql+asyncpg://...`), an async-only driver.
   - **One data layer, async only**: all models/repositories are written against `AsyncSession`. No sync mirror ever — a dual sync/async persistence layer doubles the code paths to keep correct and the test surface.
   - **Alembic migrates synchronously via the async-template `run_sync` wrapper**: migration scripts are plain sync DDL; `env.py` uses the async engine + `connection.run_sync(...)` pattern so the repo keeps a single driver (asyncpg) and a single URL format — no psycopg in the lockfile.
   - **Ingestion CLI is async internally behind a sync `asyncio.run` entrypoint** (defined fully in the ingestion spec; recorded here because it depends on this data layer). Rejected alternative: a genuinely sync ingestion path — it was the one option forcing a second persistence implementation, and ingestion is embarrassingly I/O-bound (embedding API calls dominate), so bounded-concurrency async batching is what makes repeated corpus reloads fast enough for iterative chunking/eval tuning.
6. **`eval_results.scores` is jsonb** until the eval-harness spec exists; it will be tightened to typed columns in that spec (same-commit update rule).
7. **`embedding_model` column** — makes a future re-embedding detectable rather than silent (see challenged decision #1).
8. **Connection pool bounds: `pool_size=5`, `max_overflow=5`** on the async engine in `engine.py`. Azure PostgreSQL Flexible Server's burstable tier has a low `max_connections` ceiling (B1ms: 50, of which several are reserved for system use), and Azure Container Apps may scale to multiple replicas, each holding its own pool — so the per-replica worst case must be bounded explicitly. At 10 max connections per replica, 3 replicas stay comfortably under the ceiling with headroom for Alembic, the ingestion CLI, and psql sessions. Revisit only via spec amendment if the tier changes. **Cross-spec constraint:** the math assumes a maximum of **3 replicas** — SPEC-010 (deployment) must cap Container Apps `maxReplicas` at 3, or amend this decision alongside raising it.
9. **`content_hash` covers content ‖ chunking config** — the hash input is the raw document bytes concatenated with a canonical serialization of the chunking configuration (strategy name, target chunk size, overlap). **Failure mode this prevents:** re-running ingestion with different chunk sizes while tuning retrieval metrics against the eval harness — with a content-only hash, ingestion would see an unchanged hash, silently skip re-chunking, and every subsequent eval run would score *stale chunks* while appearing to measure the new configuration. Chunking-config changes must produce a new hash so re-ingestion actually re-chunks.

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

### Async fixture pattern (binding — decided here, not during implementation)

- **pytest-asyncio pinned at 1.4.0** (already in `uv.lock`); `asyncio_mode = "auto"` stays as configured.
- **Event-loop scoping is explicit**: `asyncio_default_fixture_loop_scope = "session"` **and** `asyncio_default_test_loop_scope = "session"` in pytest config, so the session-scoped engine, function-scoped fixtures, and the tests themselves all share one loop — preempting the "attached to a different loop" failure mode.
- **Engine fixture is session-scoped**, created once with `NullPool` (tests don't exercise the production pool; pool bounds are a runtime concern per Key decision 8).
- **Connections are function-scoped**: each test gets a fresh `AsyncConnection` from the engine.
- **Rollback isolation via nested transactions**: the function-scoped fixture opens an outer transaction, binds an `AsyncSession` to the connection with `join_transaction_mode="create_savepoint"`, and rolls back the outer transaction at teardown — every test sees a clean schema, no cross-test state, no per-test truncation.
- Migration round-trip tests (AC-1/AC-2) are the exception: they run against a dedicated scratch database via subprocess/`command.upgrade`, outside the rollback fixture.

**Limitation of this fixture pattern (recorded 2026-07-25).** Savepoint-rollback fixtures verify behavior within one process and one outer transaction; they *structurally cannot catch cross-run defects* — anything they observe is rolled back, so an acceptance criterion phrased as "the second run of the CLI …" can fail in practice while these tests stay green. Cross-run criteria must be tested with separate process invocations against a real (scratch) database; the first such test is SPEC-003's `tests/test_ingest_idempotency.py`. Related hardening from the same incident: the suite now defaults to a dedicated `rag_test` database (created on demand; CI still injects its own `DATABASE_URL`) so test runs and test-debugging can never touch locally ingested corpus data — the incident that prompted this note was real corpus rows in the shared `rag` database being truncated during test-leak debugging, which made the next ingest correctly report `new` and masquerade as an idempotency failure.
