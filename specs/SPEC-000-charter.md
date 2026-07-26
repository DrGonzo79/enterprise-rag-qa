# SPEC-000 — Project Charter

**Status:** Draft — awaiting architect approval
**Date:** 2026-07-25

## Purpose

Build **enterprise-rag-qa**: a production-grade retrieval-augmented Q&A service over enterprise compliance documents (NIST AI RMF, EU AI Act, 2–3 public 10-K filings) with a scored evaluation harness, hybrid retrieval (dense + full-text), full observability, and one-command Azure deployment.

The project demonstrates production engineering discipline end to end: typed contracts, tests derived from acceptance criteria, reproducible builds, measured (not asserted) retrieval quality.

## Non-goals

- Multi-tenancy or per-user data isolation
- Authentication / SSO (the service is a demonstration deployment, not a product)
- Model fine-tuning or distillation
- Agentic / multi-step orchestration flows
- Non-English corpora
- Production SLAs, on-call, or HA topology
- Document formats beyond PDF/HTML/plain text

## Interface

This section defines the spec process itself. It binds every subsequent spec.

1. Specs live in `/specs` as `SPEC-NNN-name.md`, numbered sequentially.
2. Every spec has **exactly six sections, in this order**: Purpose · Non-goals · Interface · Key decisions · Acceptance criteria · Test plan.
3. Acceptance criteria must be **objectively testable**: each names the command, assertion, or observable state that proves it.
4. Tests are written from acceptance criteria **before** implementation.
5. Nothing is implemented until its spec is approved by the architect.
6. When implementation forces a decision change, the spec is updated **in the same commit** as the change.
7. Every implementation PR/commit references the spec it implements.

## Key decisions

### Stack (decided; challenges require a spec amendment)

| Component | Choice | Rationale |
|---|---|---|
| Language / runtime | Python 3.12 | Modern typing, broad ecosystem |
| API framework | FastAPI | Typed, async, OpenAPI for free |
| Database | Postgres 16 + pgvector (HNSW) + tsvector | **One database on purpose** — a second datastore is not justified at this scale; hybrid retrieval in one engine |
| Generation | Anthropic Claude behind a model-agnostic adapter; OpenAI as second provider | Avoids provider lock-in at the call site |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) | Cheap, strong baseline |
| Frontend | React + Vite | Minimal, cuttable |
| Packaging / deploy | Docker multi-stage; docker-compose local; GitHub Actions CI; Azure Container Apps + PostgreSQL Flexible Server via Bicep | One-command deploy is a never-cut item |

### Scope-cut ladder

When time or scope pressure forces cuts, cut **in this order** — and never below the line:

**Cuttable (first to last):** OTel traces → reranker → frontend streaming → frontend entirely → second LLM provider.

**Never cut:** eval harness · Postgres/pgvector · Docker · CI · Azure deploy.

### Provenance rule (standing, applies to every contribution)

This repository is public and MIT-licensed. All content must derive exclusively from public sources (public regulations, public filings, public documentation) and original work. No material of any kind — code, prompts, evaluation data, methodology, naming conventions — may be carried over from any contributor's employer. No employer is named anywhere in the repository, including commit messages.

## Acceptance criteria

1. Every merged spec contains exactly the six sections of §Interface, in order — verifiable by inspection of `/specs/*.md`.
2. Every implementation commit references a spec ID (`SPEC-NNN`) in its message — verifiable via `git log --oneline`.
3. No implementation file exists in the repo whose governing spec is not marked Approved — verifiable by diffing repo contents against approved spec scopes.
4. The scope-cut ladder is honored: no never-cut item is absent from the final system — verifiable against the deployed artifact at project end.

## Test plan

- Criteria 1–2 are enforced by review checklist now; a CI lint step (spec-section check + commit-message grep) may be added in a later spec — it is *cuttable* tooling, the criteria themselves are not.
- Criterion 3 is checked at each spec review: the reviewer confirms `git status`/tree contains nothing beyond approved scopes.
- Criterion 4 is checked once, at the project retrospective, against the deployed system.
