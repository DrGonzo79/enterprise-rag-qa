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
   - **After writing any test, verify it fails when the behavior under test is broken.** Break the behavior — invert a condition, delete the guard, add the consumer the code forbids — run the test, watch it fail, then restore. A test never observed failing asserts the author's intent, not the system's behavior. **Three tests in this project have passed while proving nothing:** the savepoint blind spot (the app's own sessions could not see the fixture's uncommitted rows), the verdict-prefix check, and a request-id test asserting against stub log records that were never emitted. A fourth was caught by this rule during SPEC-006's second review — the concurrency measurement read peak overlap, which timing made 2 even when three sessions were open. A fifth was SPEC-006 AC-7: it asserted the request id on `caplog` records, and `caplog` reads `LogRecord` objects before any formatter runs, so the test structurally could not observe that no formatter existed and the id reached no operator — it proved the half of KD-5 that worked and was blind to the half that did not. (Fixed in `424b667`; AC-7 now asserts on rendered output.) A sixth is SPEC-006 AC-12's "a scrape opens no database connection": it built an app with **no budget configured**, so the headroom snapshot — the only code on that path that could open one — never ran, and the zero it asserted was a statement about absence rather than about behavior. Proved by mutation: make the scrape query only when a budget is configured, and the old test passes while the corrected one fails. **That one was found and then filed as "true but untested", which is its own failure mode** — noticing the shape and reaching for the milder label. "Untested" means nobody checked; this was checked, and the check could not have failed. If a passing test's premise makes its subject unreachable, it belongs in this list, not in a softer one. A seventh was caught by this rule before shipping, during KD-16 amendment 5: the test proving `max_cost` is an **upper bound** on a query's cost ran against a four-chunk fixture, where the output term is so much larger than the input term that mutating the input bound into a plausible estimate (`len(prompt) // 4`) left the test green — the total still cleared the actual cost, for a reason that had nothing to do with the half being mutated. **A bound with two terms needs a fixture where each term dominates in turn**, or the test only ever exercises the larger one. The failure mode is uniform: the assertion was true for a reason unrelated to the behavior. Only the failing run distinguishes them, and **a test that inspects an intermediate object rather than the output is the shape to be most suspicious of** — closely followed by one whose fixture makes the quantity under test negligible.
4. Nothing is implemented until its spec is marked Approved. Check the Status line first.
   - **Approval is a review by the repository owner, not a state field.** The Status line records an approval that already happened; it is not what makes one happen. **The author of a spec may never move it out of Draft** — an implementer who sets the field is satisfying the gate by writing the thing the gate reads, which passes the check for a reason unrelated to what it protects. Only the owner moves a spec to Approved, and the commit that does so is theirs.
   - **An instruction to implement is not an approval.** "Then implement SPEC-NNN", "start on it", "do that next", a deadline, a priority, or any other scheduling statement says *when* to do the work, not that the decisions in it have been reviewed. A review reads the Key decisions and the Acceptance criteria and responds to them; nothing shorter is one. Treating the two as interchangeable is how an unreviewed design ships with a green check beside it.
   - **Amending an approved spec needs approval too, and where it comes from depends on who raised it.** Rule 4 covered Draft→Approved and said nothing about the far more common case, which is changing a decision in a spec that is already Approved and implemented. Two kinds, and they are not close:
     - **An amendment the owner asked for is approved by the asking.** The request *is* the review — it read the decision, chose between options, and said what to do. Apply it in the same commit as the code, mark it as an amendment with its date and its origin, and say in the commit that you read it as approval so the owner can push back cheaply. **SPEC-006 Key decision 16, amendment 5 (2026-08-01) is the worked example**: the owner's message named the flaw, chose which of the two overshoot sources to fix, specified the mechanism, rejected an alternative (a second semaphore), and dictated the client-facing rendering. That is a review, and asking for it to be repeated as a Draft would be ceremony.
     - **An amendment you propose unprompted stops at proposed.** Write it, argue it, and mark it **Proposed** in the spec with what it would change and what breaks without it — then stop. Do not implement it, and do not fold it into an approved amendment's commit where its approval would be inherited from something adjacent to it. The failure this prevents is the quiet one: a decision that nobody chose, shipped alongside one they did, indistinguishable afterwards.
   - **If you believe you have approval implicitly, say so and stop.** Name the spec, name what you think granted the approval, and ask for it explicitly. Do not implement, and do not set the Status line while waiting. Being blocked for one exchange is cheap; an unreviewed spec discovered after implementation is not, because the cost of changing a decision then is the code, the tests, and the review that already happened.
5. Spec + tests + code are committed together; when implementation forces a decision change, the spec is updated **in the same commit**.
6. Every implementation commit references its spec.
7. **Every claim a spec makes about the system is swept against the system — and a *guarantee* needs either a test or a stated bound.**
   - **The criterion is a claim about the system, not a claim about code.** The sweep after the third review round checked claims about code that exists — "records go to stdout" (they go to stderr), "~90 lines" (it was ~150) — and that criterion was too narrow to catch anything else. SPEC-006 KD-16's "the overshoot is bounded and computable rather than hoped for: at most `spend_rate × TTL × replicas`" **survived that sweep**, because it is a claim about an emergent property and **no single line of code contradicts it**. It was false: it described cross-replica staleness and silently omitted concurrent in-flight requests on one replica, and it was "computable" only under a bounded arrival rate that KD-14 explicitly leaves unbounded. Sweep for anything asserting what the system does, is, cannot do, or costs — every "at most", "bounded", "never", "always", "cannot", "guaranteed", and every formula.
   - **A guarantee needs a test that fails when it is broken, or a stated bound naming what it does not cover.** Preferably both. This is rule 3 pointed at prose instead of at assertions: an untested guarantee is true for whatever reason its author had in mind, which is exactly the thing rule 3 says only a failing run can distinguish. A formula in a spec is an assertion with no test runner attached, and it decays the same way — silently, and in the direction that flatters the design.
   - **Where the guarantee has a term nobody can bound, say so and name it.** "Bounded by `ceiling + one worst-case query`, and separately by `(N−1) × TTL × arrival rate × per-query cost`, which is zero at one replica and deferred to SPEC-010" is a guarantee. "Bounded and computable rather than hoped for" was a hope written in the indicative.
   - **Second instance, found one day after this rule was written** *(2026-08-02)*: SPEC-006 KD-14 accepted no rate limiting partly "because keys are not public". That is a guarantee about the system, it has no test, and it goes false the moment a browser holds the read key — which is what SPEC-009 does by existing. The claim was true when written and true of every client the repo has today; what was missing was the boundary. Corrected to name the class of client it holds for, with the exposure recorded and the mechanism handed to SPEC-010. **A guarantee that is true of today's clients and silently false of tomorrow's needs its scope written into it**, because the sentence outlives the assumption that made it true.
   - Sweep when a spec is amended, and when code the spec describes changes.

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
