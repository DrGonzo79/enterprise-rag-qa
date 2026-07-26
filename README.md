# enterprise-rag-qa

Production-grade retrieval-augmented Q&A over enterprise compliance documents —
with a scored evaluation harness, hybrid retrieval, and one-command Azure deployment.

> **Status: Week 1 of 7 — building in public.** Specs first, code second.
> Target ship: September 15, 2026. Follow the build:
> [LinkedIn](https://www.linkedin.com/in/aiwithjustin)

## What this is

Most RAG demos answer questions. Few of them measure whether the answers are
any good. This project centers the part that gets skipped: an evaluation
harness that scores retrieval quality, answer correctness, groundedness, and
refusal accuracy against a golden dataset.

The corpus is public compliance material — NIST AI Risk Management Framework,
the EU AI Act, and public 10-K filings — because regulatory text is where
grounding and citation actually matter.

## Quickstart (local)

```bash
git clone https://github.com/DrGonzo79/enterprise-rag-qa && cd enterprise-rag-qa
uv sync                              # install (Python 3.12, uv-managed)
cp .env.example .env                 # then add your OPENAI_API_KEY
docker compose up -d postgres        # pgvector Postgres on :5432
uv run alembic upgrade head          # create the schema
uv run python -m scripts.fetch_corpus            # download the three source documents
uv run python -m rag_qa.ingest corpus/ --dry-run # inspect chunk counts + cost (no DB, no API)
uv run python -m rag_qa.ingest corpus/           # embed and load (~$0.005)
uv run pytest                        # green = you're set
```

Local runs read `.env` automatically (exported environment variables always
take precedence, so Docker/CI behavior is unchanged). If the EUR-Lex download
is blocked by its WAF challenge, the fetch script prints manual-download
instructions.

## Architecture

Ingestion → chunking → embedding → Postgres (pgvector + tsvector)
Query → hybrid retrieval (vector + full-text, RRF) → Claude → cited answer
Every request logged with latency, token counts, and cost.

**Stack:** Python 3.12 · FastAPI · Postgres 16 + pgvector · Anthropic Claude
(model-agnostic adapter) · Docker · GitHub Actions · Azure Container Apps + Bicep

## Key design decisions

**Postgres + pgvector instead of a dedicated vector database.** One system
provides vector search (HNSW), full-text search (tsvector), and the relational
layer for query logs and evaluation runs. At this scale a second datastore adds
operational complexity without a corresponding payoff — and on Azure it's one
Flexible Server instead of two services to manage and pay for.

**Hybrid retrieval, not vector-only.** Regulatory text is dense with terms of
art and citations ("Article 6(2)"). Embeddings blur exact terms; full-text
search nails them. Reciprocal Rank Fusion combines both.

**Refusal is a tested capability.** The evaluation set deliberately includes
questions the corpus can't answer. Declining to answer is scored, not assumed.

## Spec-driven development

Every module gets a written spec before any code: purpose, non-goals,
interface, key decisions, acceptance criteria, and test plan. Tests are written
from the acceptance criteria; implementation follows. See [`/specs`](./specs).

## Roadmap

- [x] Charter, scaffold, and data model specs
- [ ] Ingestion pipeline (chunking, embedding, idempotent re-ingestion)
- [ ] Hybrid retrieval service
- [ ] Generation with citations and refusal path
- [ ] **Evaluation harness** — retrieval metrics, LLM-as-judge, groundedness
- [ ] Observability: structured logs, per-request cost and latency
- [ ] Azure deployment via Bicep + CD pipeline
- [ ] Minimal React frontend

## Evaluation results

_Published here once the harness runs — retrieval recall@k and MRR, answer
correctness, groundedness, and refusal accuracy across a 50-question golden set._

## License

MIT