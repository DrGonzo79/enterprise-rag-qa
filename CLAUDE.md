# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Retrieval-augmented Q&A over public compliance documents (NIST AI RMF, EU AI Act, public 10-K filings) with a scored evaluation harness and hybrid retrieval. Building in public; target ship September 15, 2026. Ingestion, hybrid retrieval, generation, and the HTTP API are implemented under SPEC-003 through SPEC-006; the eval harness (SPEC-007), frontend (SPEC-009), and deployment (SPEC-010) are not yet written.

## Commands

Everything runs through uv:

```bash
uv sync                          # install (CI uses --frozen)
uv run pytest                    # all tests
uv run pytest tests/test_scaffold.py::test_healthz   # single test
uv run ruff check . && uv run ruff format --check .  # lint
uv run pyright                   # type-check (strict on src/, basic on tests/)
docker compose up -d             # api on :8000 + pgvector Postgres on :5432
```

CI (`.github/workflows/ci.yml`) runs one job with sequential gates **lint → type-check → test**, with a pgvector service container and `DATABASE_URL=postgresql+asyncpg://rag:rag@localhost:5432/rag`. Run the same three commands in the same order locally before pushing. Pre-commit runs ruff only; pyright is deliberately CI-only.

pytest-asyncio is in `auto` mode — async tests need no marker.

## Spec-driven workflow (binding — from SPEC-000)

Specs in `/specs` precede all code. The process:

1. Specs are `specs/SPEC-NNN-name.md`, numbered sequentially, with **exactly six sections in order**: Purpose · Non-goals · Interface · Key decisions · Acceptance criteria · Test plan.
2. Acceptance criteria must be objectively testable — each names the command, assertion, or observable state that proves it.
3. Tests are written **from the acceptance criteria, before implementation**.
   - **After writing any test, verify it fails when the behavior under test is broken.** Break the behavior — invert a condition, delete the guard, add the consumer the code forbids — run the test, watch it fail, then restore. A test never observed failing asserts the author's intent, not the system's behavior. **Three tests in this project have passed while proving nothing:** the savepoint blind spot (the app's own sessions could not see the fixture's uncommitted rows), the verdict-prefix check, and a request-id test asserting against stub log records that were never emitted. A fourth was caught by this rule during SPEC-006's second review — the concurrency measurement read peak overlap, which timing made 2 even when three sessions were open. A fifth was SPEC-006 AC-7: it asserted the request id on `caplog` records, and `caplog` reads `LogRecord` objects before any formatter runs, so the test structurally could not observe that no formatter existed and the id reached no operator — it proved the half of KD-5 that worked and was blind to the half that did not. (Fixed in `424b667`; AC-7 now asserts on rendered output.) A sixth is SPEC-006 AC-12's "a scrape opens no database connection": it built an app with **no budget configured**, so the headroom snapshot — the only code on that path that could open one — never ran, and the zero it asserted was a statement about absence rather than about behavior. Proved by mutation: make the scrape query only when a budget is configured, and the old test passes while the corrected one fails. **That one was found and then filed as "true but untested", which is its own failure mode** — noticing the shape and reaching for the milder label. "Untested" means nobody checked; this was checked, and the check could not have failed. If a passing test's premise makes its subject unreachable, it belongs in this list, not in a softer one. The failure mode is uniform: the assertion was true for a reason unrelated to the behavior. Only the failing run distinguishes them, and **a test that inspects an intermediate object rather than the output is the shape to be most suspicious of.**
4. Nothing is implemented until its spec is marked Approved. Check the Status line first.
   - **Approval is a review by the repository owner, not a state field.** The Status line records an approval that already happened; it is not what makes one happen. **The author of a spec may never move it out of Draft** — an implementer who sets the field is satisfying the gate by writing the thing the gate reads, which passes the check for a reason unrelated to what it protects. Only the owner moves a spec to Approved, and the commit that does so is theirs.
   - **An instruction to implement is not an approval.** "Then implement SPEC-NNN", "start on it", "do that next", a deadline, a priority, or any other scheduling statement says *when* to do the work, not that the decisions in it have been reviewed. A review reads the Key decisions and the Acceptance criteria and responds to them; nothing shorter is one. Treating the two as interchangeable is how an unreviewed design ships with a green check beside it.
   - **If you believe you have approval implicitly, say so and stop.** Name the spec, name what you think granted the approval, and ask for it explicitly. Do not implement, and do not set the Status line while waiting. Being blocked for one exchange is cheap; an unreviewed spec discovered after implementation is not, because the cost of changing a decision then is the code, the tests, and the review that already happened.
5. Spec + tests + code are committed together; when implementation forces a decision change, the spec is updated **in the same commit**.
6. Every implementation commit references its spec.

**Commit convention:** `SPEC-NNN: short description`

Current spec state (2026-07-26): SPEC-001 (scaffold), SPEC-002 (data model), SPEC-003 (ingestion), SPEC-004 (retrieval), SPEC-005 (generation), SPEC-006 (API) are Approved and implemented. SPEC-008 (request records and failure signal) is Approved and implemented. SPEC-000 (charter) is Draft.

## Stack (locked — challenges require a spec amendment)

- Python 3.12 · FastAPI · uv (lock committed, `--frozen` in CI)
- Postgres 16 + pgvector (HNSW) + tsvector — one database
- Generation: Anthropic Claude behind a model-agnostic adapter; OpenAI as second provider
- Embeddings: OpenAI `text-embedding-3-small` (1536-dim)
- Frontend: React + Vite (minimal, cuttable)
- Docker multi-stage · docker-compose local · GitHub Actions CI · Azure Container Apps + PostgreSQL Flexible Server via Bicep

**Why pgvector over a dedicated vector DB:** one system provides vector search (HNSW), full-text search (tsvector), and the relational layer for query logs and eval runs. At this scale a second datastore adds operational complexity without payoff — and on Azure it's one Flexible Server instead of two services.

**Why hybrid retrieval, not vector-only:** regulatory text is dense with terms of art and exact citations ("Article 6(2)"). Embeddings blur exact terms; full-text search nails them. Reciprocal Rank Fusion combines both. Refusal is a tested capability — the eval set includes unanswerable questions, and declining is scored.

## Scope-cut ladder

Under time pressure, cut in this order and never below the line:

- **Cuttable (first to last):** OTel traces → reranker → frontend streaming → frontend entirely → second LLM provider
- **Never cut:** eval harness · Postgres/pgvector · Docker · CI · Azure deploy · README quality

## Hard rule: provenance

This repo is public (MIT). It must **never** contain anything derived from the owner's employer's work — no code, prompts, evaluation data, methodology, or naming conventions. No employer is named anywhere, including commit messages. If any contribution even approaches that line, stop and flag it explicitly before proceeding.
