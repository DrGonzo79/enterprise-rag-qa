# SPEC-001 — Repository Scaffold & Tooling

**Status:** Approved 2026-07-25 · Amended 2026-07-25 (Key decision 6: load `.env` at entrypoints)
**Date:** 2026-07-25
**Depends on:** SPEC-000

## Purpose

Establish repo tooling such that a fresh clone reaches green tests with two commands (`uv sync`, `uv run pytest`), local services start with one (`docker compose up`), and CI enforces the same gates as the local toolchain, in the same order.

## Non-goals

- Application logic of any kind (retrieval, generation, ingestion) — a minimal `/healthz` endpoint exists **only** so the `api` container has something to health-check
- Database schema or migrations (SPEC-002)
- Azure deployment (later spec; Bicep is out of scope here)
- Frontend scaffold (later spec; cuttable per ladder)

## Interface

Files this spec creates, and their contract:

| File | Contract |
|---|---|
| `pyproject.toml` | uv-managed; Python 3.12; src layout; importable package **`rag_qa`**; runtime deps: fastapi, uvicorn; dev group: pytest, pytest-asyncio (`asyncio_mode = "auto"`), httpx, ruff, pyright, pre-commit, pyyaml |
| `uv.lock` | Committed; CI installs `--frozen` |
| `src/rag_qa/__init__.py` | Exposes `__version__` |
| `src/rag_qa/main.py` | FastAPI app; `GET /healthz` → `200 {"status": "ok"}` |
| `tests/test_scaffold.py` | Tests derived from §Acceptance criteria |
| `.env.example` | Keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DATABASE_URL`, `LOG_LEVEL` — values empty or local-only defaults |
| `LICENSE` | MIT |
| `.gitignore` | Excludes `.env`, `.venv/`, caches, build artifacts |
| `.pre-commit-config.yaml` | Hooks: ruff check (`--fix`), ruff format, trailing-whitespace, end-of-file-fixer. **Pyright is CI-only** (too slow for commit hook) |
| `Dockerfile` | Multi-stage: uv builder → `python:3.12-slim` runtime; runs uvicorn on :8000 |
| `docker-compose.yml` | Services: `api` (build from Dockerfile, healthcheck on `/healthz`) and `postgres` (`pgvector/pgvector:pg16`, named volume, `pg_isready` healthcheck); `api` `depends_on: postgres: condition: service_healthy` |
| `.github/workflows/ci.yml` | One job, sequential steps **lint → type-check → test**; `uv sync --frozen`; Postgres (pgvector) service container available to tests |

Tooling configuration (in `pyproject.toml`): ruff is both linter and formatter; pyright strict on `src/`, basic on `tests/`.

## Key decisions

1. **uv over pip/poetry** — single tool for lock, sync, run; `--frozen` gives CI/local parity.
2. **Pyright out of pre-commit, in CI** — commit hooks stay sub-second; the type gate still blocks merge.
3. **Single CI job with sequential steps** — mirrors the local gate order exactly; no matrix (one Python version is a project decision, not a library).
4. **`.gitignore` added although not in the original brief** — required to keep `.env` (secrets) and `.venv` out of a public repo; recorded here per the same-commit spec-update rule.
5. **Minimal `/healthz` now** — the alternative (no api healthcheck until SPEC-003) would make AC-2 untestable; the endpoint is scaffold, not application logic.
6. **Entrypoints load `.env` via python-dotenv, real environment wins** *(amended 2026-07-25)*. The gap this closes: `engine.py` reads `os.environ["DATABASE_URL"]` directly and nothing loaded `.env`, so bare local CLI runs (`python -m rag_qa.ingest`, `alembic upgrade head`) died with `KeyError` on a fresh clone — while Docker and CI masked it because compose/Actions inject the variables. That made this spec's "stranger can clone and run" purpose pass in CI and fail in practice. Fix contract:
   - `rag_qa/env.py::load_env()` calls `load_dotenv(find_dotenv(usecwd=True), override=False)`. **`override=False` is the load-bearing choice**: a variable already present in the real environment is never touched, so container and CI behavior is bit-for-bit unchanged; `.env` only fills gaps on a bare shell. `usecwd=True` because the installed package lives under `.venv`, from which dotenv's default upward search would never find the repo root.
   - Call sites are the process entrypoints only — ingest CLI (`rag_qa/ingest/__main__.py`), the FastAPI module (`rag_qa/main.py`), and `alembic/env.py` — never library code; imports must not mutate the environment as a side effect of anything but starting a process.
   - Rejected: pydantic-settings — a full settings model is worth adopting when there is a settings *schema* to validate; today the need is exactly "read `.env` if present", and python-dotenv is the smallest tool that does it.

## Acceptance criteria

- **AC-1** `uv sync && uv run pytest` exits 0 on a fresh clone.
- **AC-2** `docker compose up -d && docker compose ps` shows both `api` and `postgres` with status `healthy`; `curl localhost:8000/healthz` returns `200`.
- **AC-3** The CI workflow passes on the scaffold commit, with steps executing in order lint → type-check → test.
- **AC-4** `pre-commit run --files <file>` fails on a deliberately unformatted Python file and passes after `ruff format` fixes it.
- **AC-5** *(amended)* With no `DATABASE_URL`/`OPENAI_API_KEY` exported and only a `.env` in the working directory, the ingest CLI proceeds past configuration to the database connection attempt (no `KeyError`); with the same variable both exported and in `.env`, the exported value is used.

## Test plan

Written **before** implementation, in `tests/test_scaffold.py`:

- `test_healthz` — ASGI-level: `GET /healthz` → 200, `{"status": "ok"}` (backs AC-2's endpoint contract without Docker)
- `test_package_version` — `rag_qa.__version__` exists (backs AC-1: package imports)
- `test_env_example_keys` — `.env.example` contains exactly the four required keys, no non-empty secret values
- `test_compose_contract` — parse `docker-compose.yml`: both services present, postgres image is `pgvector/pgvector:pg16`, both define healthchecks, api depends on postgres `service_healthy`
- `test_ci_step_order` — parse `ci.yml`: steps named Lint / Type-check / Test appear in that order
- `test_license_mit` — `LICENSE` first line contains "MIT License"

Amendment (AC-5), in `tests/test_env_loading.py`:

- `test_cli_starts_with_only_dotenv` — subprocess CLI run, stripped environment, `.env` pointing `DATABASE_URL` at an unreachable loopback port: asserts the failure is the connection attempt naming that port, not `KeyError` (proving the value was read from `.env`)
- `test_real_env_var_wins_over_dotenv` — exported variable survives `load_env()` unchanged; a variable present only in `.env` is filled in
- `test_load_env_is_a_noop_without_dotenv` — no `.env` anywhere: environment untouched

Verified by command (not pytest — they exercise the outer toolchain itself):
- AC-2 end-to-end: `docker compose up -d`, poll `docker compose ps` until both healthy, `curl /healthz`, `docker compose down`
- AC-3: observed on the first push (locally approximated by running the three gate commands in CI order)
- AC-4: demonstrated once with a scratch unformatted file, output captured in the implementation session
