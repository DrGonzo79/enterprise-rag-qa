# SPEC-006 — HTTP API

**Status:** Approved — 2026-07-26 (review amendments: Key decision 16 chooses a daily spend ceiling with a circuit breaker over per-IP limits and cached-demo mode, with the enforcement backstop handed to SPEC-010; Key decision 10 records the rejected single-statement CTE alternative that would eliminate the two-connection deadlock class rather than bound it, and enumerates what `RESERVED` reserves)
**Date:** 2026-07-26
**Depends on:** SPEC-004 (retrieval), SPEC-005 (generation)

## Purpose

The service surface: `POST /query` (JSON and SSE), `POST /ingest`, `GET /health`, `GET /metrics`, behind API-key authentication, with an auto-generated OpenAPI document that is accurate rather than merely present.

This spec owns the boundary between HTTP and the two libraries beneath it. Retrieval and generation are deliberately transport-ignorant — `Retriever` and `Generator` know nothing about requests, headers, or streams — so everything that makes them a *service* lands here: authentication, error mapping, framing, concurrency bounds, and the request identifier that stitches their log records together.

Three obligations shape the design:

1. **HTTP status describes the transport; `verdict` describes the outcome.** A refusal is a successful request (Key decision 1). Collapsing the two would make a charter-scored capability invisible to the field clients read.
2. **The connection pool is a hard external bound, not a tunable.** SPEC-002 Key decision 8 fixes it against Azure's `max_connections` ceiling, and SPEC-004 spends two connections per retrieval. Concurrency is therefore bounded *above* the pool, explicitly, rather than discovered at it (Key decision 10).
3. **Every request is traceable end to end.** One identifier flows from the middleware through retrieval and generation into `query_log` and every log record emitted in between — without changing either library's approved signature (Key decision 5).

## Non-goals

- Authentication of *users* — API keys identify a caller, not a person. SPEC-000 rules out SSO and per-user isolation; keys are a deployment gate, not an identity system.
- Rate limiting and quota enforcement (see Key decision 14 — deliberate, with the trigger for revisiting stated)
- The frontend — React + Vite is its own spec and is cuttable; this spec defines the contract it would consume
- Log formatting, exporters, dashboards, and traces — SPEC-006 defines the request-id *seam*; SPEC-008 owns what is done with it
- Evaluation endpoints — SPEC-007 runs offline against the libraries, not over HTTP
- Multi-tenancy, per-caller cost attribution, or usage billing (SPEC-005 non-goal, unchanged)
- Async job infrastructure — see Key decision 12 for what `/ingest` does instead, and why
- CORS, CSRF, TLS termination, and ingress configuration — deployment concerns owned by SPEC-010
- API versioning (`/v1/…`) — there is no client to break; adding a prefix costs nothing later and buys nothing now

## Interface

### Modules

```
src/rag_qa/api/
    __init__.py       # create_app() — the application factory
    app.py            # FastAPI construction, lifespan, router mounting
    deps.py           # Settings, Retriever/Generator providers, auth dependencies
    auth.py           # API-key verification, scopes
    context.py        # request_id ContextVar + log-record factory (SPEC-008 seam)
    middleware.py     # request id, response header, error envelope
    errors.py         # exception -> status mapping, ErrorResponse
    schemas.py        # request/response Pydantic models (OpenAPI's source of truth)
    sse.py            # SSE framing: event -> wire, heartbeats, terminal error
    concurrency.py    # RESERVED_CONNECTIONS, derived query bound (Key decision 10)
    budget.py         # daily spend ceiling + circuit breaker (Key decision 16)
    routes/
        query.py      # POST /query  (JSON + SSE)
        ingest.py     # POST /ingest
        health.py     # GET  /health
        metrics.py    # GET  /metrics
    metrics.py        # in-process counters (no database — Key decision 9)
```

Superseded / amended in place, in the implementation commit (SPEC-000 rule 6):

- **`src/rag_qa/generation/api.py` is removed.** Its `POST /ask` becomes `POST /query` (Key decision 2). **SPEC-005 AC-11 must be amended in the same commit** — it names `/ask`.
- **`src/rag_qa/main.py`** stops wiring the app at import time and becomes a thin `app = create_app()` (Key decision 11). `GET /healthz` is unchanged and stays exactly where SPEC-001 put it.

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/query` | read key | Retrieve + answer. JSON or SSE. |
| `POST` | `/ingest` | **admin key** | Ingest documents already present server-side. Dry-run by default. |
| `GET` | `/health` | none | Readiness: dependencies checked, degradations named. |
| `GET` | `/healthz` | none | Liveness (SPEC-001, unchanged): process is up. Touches nothing. |
| `GET` | `/metrics` | **admin key** | Prometheus text format (Key decision 8). |
| `GET` | `/docs`, `/openapi.json` | none | Interactive docs and schema. Shape is public; data is not. |

### `POST /query`

```jsonc
// request
{
  "question": "What are the obligations for high-risk AI systems?",
  "k": 8,                                  // 1..50, default 8
  "filters": {"doc_types": ["regulation"], "document_ids": [], "source_uris": []},
  "stream": false                          // false -> application/json, true -> text/event-stream
}
```

```jsonc
// 200 application/json
{
  "request_id": "01J…",
  "verdict": "answered",                   // answered | insufficient_evidence | truncated
                                           //   | provider_refused | error
  "answer": "Providers must complete a conformity assessment [1] …",
  "citations": [
    {
      "marker": 1,
      "chunk_id": "…",
      "section_path": "EU AI Act › CHAPTER III › SECTION 2 › Article 16",
      "document_title": "Regulation (EU) 2024/1689",
      "source_uri": "https://eur-lex.europa.eu/…",
      "doc_type": "regulation"
    }
  ],
  "dropped_markers": [],                   // SPEC-005 KD-9, surfaced not hidden
  "usage": {
    "generator_identity": "anthropic:claude-sonnet-5",
    "prompt_tokens": 4120, "completion_tokens": 210,
    "cost_usd": "0.010250", "latency_ms": 3120,
    "prompt_version": "v1"
  }
}
```

`citations` carries `section_path` in **both** modes — it is the whole point of SPEC-005's citation design, and a client must never have to resolve a `chunk_id` to render "Article 16". `cost_usd` is a **string**, not a float: it is `numeric(10,6)` in the database and JSON floats are binary, so serializing a Decimal as a number is a lossy round-trip on a money column.

### SSE mode (`stream: true`)

`Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no` (a buffering proxy silently defeats streaming). Frames are unnamed `data:` lines carrying JSON with a `type` discriminator (Key decision 3):

```
: keepalive                                  ← comment frame, ignored by clients

data: {"type":"verdict","verdict":"answered"}

data: {"type":"text","text":"Providers must complete a "}

data: {"type":"citation","marker":1,"chunk_id":"…","section_path":"EU AI Act › … › Article 16"}

data: {"type":"complete","verdict":"answered","usage":{…}}
```

Event order is SPEC-005's, unchanged: `verdict` first, then `text`/`citation` interleaved, `complete` last. Mapping is one-to-one onto `VerdictEvent` / `TextDelta` / `CitationEvent` / `AnswerComplete`, so the transport adds no semantics of its own.

**The buffered verdict token across the transport boundary (Key decision 4).** SPEC-005 AC-7a buffers the raw verdict token until the first newline *inside the parser*, which sits server-side of this boundary — so `ANSWERED` never enters the SSE byte stream in any form. The first frame carries an already-parsed value. Three consequences, each of which is asserted rather than assumed:

1. **No frame may contain a verdict token or any prefix of one**, and concatenating every `text` frame must equal the non-streaming `answer` for the same response. AC-5 asserts this at the byte level over every split point, because the realistic bug is a *partial* leak — a provider chunk boundary landing inside `ANSWERED` and a naive implementation flushing what it has.
2. **Time-to-first-frame is bounded below by the model's time-to-first-newline**, and with adaptive thinking on (SPEC-005 KD-6) that is after the thinking phase. The stream can therefore be silent for seconds before anything arrives — which is precisely why heartbeat comment frames are mandatory, not decorative: an idle-timeout proxy would otherwise kill a healthy request.
3. **A stream ending mid-verdict yields `error` with zero `text` frames** (SPEC-005 `finish()`), which over HTTP is a `verdict` frame of `"error"` followed by `complete`, and no text at all.

**Errors after the stream has started cannot change the status code** — headers went out with the 200. A failure mid-stream emits a terminal `{"type":"error","code":…,"message":…}` frame and closes. Clients must treat a stream that ends without a `complete` frame as failed.

**Client disconnect still writes the `query_log` row.** The tokens were spent whether or not anyone was listening; dropping the row would make cost silently under-report exactly when a client is misbehaving.

### `POST /ingest`

```jsonc
// request
{"paths": ["corpus/"], "dry_run": true}
```

Admin key required. `dry_run: true` (the default) runs SPEC-003's dry-run path — no database writes, no embedding calls, no spend — and returns the manifest: per-document chunk counts, dropped tables, and the estimated embedding cost. `dry_run: false` performs the ingest and returns the same manifest with per-document verdicts (`new` / `unchanged` / `replace`).

Three bounds, each from a named failure (Key decision 12): a **Postgres advisory lock** makes ingestion single-flight across replicas (409 if held); `paths` resolve **inside the repo's corpus directory only** (no arbitrary server paths, no upload); and a real ingest whose dry-run manifest exceeds `INGEST_MAX_CHUNKS` is rejected with 413 naming the CLI.

### `GET /health` and `GET /healthz`

`/healthz` (SPEC-001) is **liveness**: `200 {"status":"ok"}`, no dependency checks, no database. It stays exactly as it is, and remains what `docker-compose` health-checks.

`/health` is **readiness**, and reports rather than merely passes:

```jsonc
{
  "status": "ok",                          // ok | degraded | unavailable
  "checks": {
    "database": {"ok": true, "latency_ms": 3},
    "migrations": {"ok": true, "revision": "0004"},
    "corpus": {"ok": true, "chunks": 358, "embedder_identity": "openai:text-embedding-3-small"},
    "generator": {"ok": true, "identity": "anthropic:claude-sonnet-5"}
  }
}
```

`degraded` → 200 (serving, something is off); `unavailable` → 503. The generator check is configuration-only — it never spends a token to answer a health probe.

### Authentication

`X-API-Key` header, two scopes: **read** (`/query`) and **admin** (`/ingest`, `/metrics`). Keys come from `RAG_QA_API_KEY` / `RAG_QA_ADMIN_API_KEY`; comparison is `hmac.compare_digest`. Missing or unknown key → **401**; valid read key on an admin route → **403**. The distinction is load-bearing: 401 says *authenticate*, 403 says *you are authenticated and it will not help*, and collapsing them sends a caller into a retry loop that cannot succeed.

**If no key is configured the app refuses to start** unless `RAG_QA_ALLOW_ANONYMOUS=1` is set explicitly. A service whose auth silently disables itself when a variable is unset is worse than one with no auth, because it looks protected.

### Error envelope

```jsonc
{"error": {"code": "embedder_mismatch", "message": "…", "request_id": "01J…"}}
```

| Condition | Status | `code` |
|---|---|---|
| Malformed body / bad types (Pydantic) | 422 | `validation_error` |
| Blank or whitespace-only `question` | 422 | `validation_error` |
| Missing / unknown API key | 401 | `unauthenticated` |
| Read key on an admin route | 403 | `forbidden` |
| `EmbedderMismatchError` (SPEC-004) | 503 | `embedder_mismatch` |
| `EmptyCorpusError` (SPEC-004) | 503 | `empty_corpus` |
| Ingest already running (advisory lock held) | 409 | `ingest_in_progress` |
| Ingest work exceeds `INGEST_MAX_CHUNKS` | 413 | `ingest_too_large` |
| Over concurrency bound / pool timeout | 503 + `Retry-After` | `overloaded` |
| Daily spend ceiling reached (Key decision 16) | 503 + `Retry-After` | `budget_exhausted` |
| Provider transport failure or timeout | 502 | `upstream_error` |
| `UnknownModelError` at request time | 500 | `misconfigured` |
| Unhandled exception | 500 | `internal_error` |
| **Model refused / declined / truncated / malformed verdict** | **200** | — (`verdict` field) |

`UnknownModelError` appearing at request time is a **bug, not a runtime condition**: SPEC-005 KD-10 raises it at client construction, so a correctly built app cannot reach it. The row exists because a 500 with a named code beats an unhandled traceback, and because that specific 500 means "this deployment was built without a pricing row" — see Key decision 13.

### Request identifier

Middleware assigns a request id (accepting a sanitized inbound `X-Request-ID`, else generating one), stores it in a `ContextVar`, echoes it on every response including errors, and includes it in every log record via a logging filter. SPEC-004's and SPEC-005's existing log records gain the field **without either module changing** (Key decision 5).

### Configuration

| Variable | Default | Role |
|---|---|---|
| `RAG_QA_API_KEY` | — | read scope; required unless anonymous is explicitly allowed |
| `RAG_QA_ADMIN_API_KEY` | — | admin scope |
| `RAG_QA_ALLOW_ANONYMOUS` | unset | explicit opt-out of auth for local runs |
| `RAG_QA_MAX_CONCURRENT_QUERIES` | *derived* | see Key decision 10; default computed from the pool constants |
| `RAG_QA_SSE_HEARTBEAT_SECONDS` | 15 | comment-frame interval |
| `INGEST_MAX_CHUNKS` | 5000 | bound on a synchronous HTTP ingest |
| `RAG_QA_DAILY_BUDGET_USD` | unset (off) | daily spend ceiling; unset disables the breaker (Key decision 16) |
| `RAG_QA_BUDGET_REFRESH_SECONDS` | 30 | TTL of the cached `query_log` spend total; bounds cross-replica overshoot |

### New dependencies

None. FastAPI, Starlette, and Pydantic are already present; SSE is a `StreamingResponse` and Prometheus text format is a string.

## Key decisions

1. **HTTP status describes the transport; `verdict` describes the outcome — every verdict returns 200. Flagged, arguing against the obvious choice.** The obvious mapping sends a refusal or a provider decline to 4xx/5xx: something didn't work, say so in the status line. Rejected for four reasons that compound. **(a)** Refusal is a *charter-scored capability* (SPEC-005 KD-3) — "declining correctly" is a success, and a 4xx would encode the project's headline feature as a failure. **(b)** Clients read the status first; a 503 hides the `verdict` field that exists precisely so refusal is machine-readable rather than string-matched. **(c)** 5xx invites retries, and a retried provider refusal refuses again — at full token cost, twice. **(d)** Error-rate SLOs and dashboards would count normal, correct behavior as outages, so the one number an operator watches would be dominated by non-events. The rule generalizes cleanly: **status answers "did the service work", `verdict` answers "what happened"** — and the two questions have genuinely different answers when a model declines to speculate.
2. **`/query` supersedes `/ask` with no alias; `/healthz` is kept and `/health` added alongside it. Flagged — the asymmetry is deliberate.** Both are renaming questions and they resolve opposite ways, so the test is stated rather than the outcome: *does anything outside this repository depend on the path, and would the two names mean different things?* `/ask` has **no external dependents** (SPEC-005 shipped it days ago; no client, no compose reference, no documentation), and `/ask` and `/query` would mean the same thing — so it is renamed outright and an alias would be pure carrying cost. `/healthz` **does** have external dependents (the `docker-compose` healthcheck, SPEC-001 AC-2) *and* the two names can mean genuinely different things: liveness versus readiness. Renaming it would amend an approved spec's acceptance criterion and — worse — point the container healthcheck at an endpoint that checks the database, so a Postgres blip would restart a perfectly healthy API container. **That is a real anti-pattern, not a naming preference**, and it is the actual reason the split is kept rather than a rationalization of an inconvenient rename.
3. **SSE frames are unnamed `data:` lines with a `type` discriminator, not named SSE events. Flagged, arguing against the obvious choice.** The obvious framing is `event: verdict` / `event: text`, which is what SSE's named-event mechanism is for. Rejected: with named events an `EventSource` client must `addEventListener` for **each** type, so adding an event type is a breaking change for every client that fails to add a listener — the new events land nowhere and are silently dropped. Unnamed frames all arrive at `onmessage`, a client switches on `type`, and an unknown type is visibly unknown rather than invisible. It also keeps the SSE and JSON payloads structurally identical, so one set of schemas documents both. **The payload is JSON for a framing reason, not a convenience one:** SSE terminates a field at `\n`, and a raw newline inside answer prose would split one logical event across two frames. `json.dumps` escapes newlines, which makes the framing correct by construction rather than by remembering to escape.
4. **The verdict token never crosses the transport boundary, and the cost of that is a silent stream — so heartbeats are mandatory.** SPEC-005 AC-7a's buffering is server-side, so the wire carries a parsed verdict, never the token. The consequence is that nothing at all can be sent until the model emits its first newline, which with adaptive thinking (SPEC-005 KD-6) is after the thinking phase — potentially many seconds of silence on a healthy request. Comment frames (`: keepalive`) every 15 s keep the connection alive through idle-timeout proxies and let a client distinguish "thinking" from "dead". **This is the transport-layer bill for Key decisions 6 and 7 of SPEC-005**, and it is worth paying for the same reason those decisions were made: the alternative is a verdict the model committed to before reasoning.
5. **The request id travels in a `ContextVar` with a logging filter, not as a parameter threaded through `retrieve()` and `answer()`. Flagged, arguing against the obvious choice.** Explicit parameter passing is the better default in almost every case, and it is the obvious design here. Rejected because it would change **two approved interfaces** (SPEC-004's `Retriever.retrieve`, SPEC-005's `Generator.answer`/`stream_answer`) to carry a value neither library has any use for — and the whole point of both is that they are transport-ignorant libraries that SPEC-007 calls directly, with no HTTP request in sight. A `ContextVar` set by middleware plus a logging filter attaches the id to **every record emitted during the request**, including the ones SPEC-004 and SPEC-005 already emit, with zero changes to either. `contextvars` copy into tasks at creation, so SPEC-004's `asyncio.gather` branches inherit it — asserted in AC-7, because that propagation is the load-bearing assumption and it is exactly the kind of thing that breaks silently under a refactor. **Accepted cost, stated:** a `ContextVar` is ambient state, so a log line written outside any request has no id — which is correct (there is no request) but must not be mistaken for a bug.
6. **An inbound `X-Request-ID` is sanitized, never trusted verbatim.** Accepting a caller's id is genuinely useful for correlating across a client's own traces, and it is why the header is honored at all. But it is attacker-controlled text going straight into log records: newlines forge log entries, and unbounded length inflates every line of a request's logging. Accepted only if it matches `^[A-Za-z0-9_.:-]{1,64}$`; anything else is silently replaced with a generated id rather than rejected, because failing a request over a cosmetic header would be a worse outcome than ignoring it.
7. **Two key scopes, and a service with no key configured refuses to start.** Read and admin are separated because `/ingest` **spends money** (embedding calls) and mutates the corpus, while `/query` reads — a single key would mean anyone who can ask a question can also trigger spend. The refusal-to-start rule matters more than the split: the common failure is a deployment where the key variable was never set, and a service that then serves everything unauthenticated *looks* protected while being open. Failing at boot converts a silent security hole into a deployment error, which is the trade this project keeps making (SPEC-004 KD-4, SPEC-005 KD-10).
8. **`/metrics` requires the admin key. Flagged, arguing against the obvious choice.** The convention is an unauthenticated `/metrics` — nearly every Prometheus deployment does this, and scrapers are awkward to authenticate. **The convention rests on a precondition this deployment does not meet:** it assumes the endpoint sits on a private network or a separate port that the internet cannot reach. This is a single Azure Container App with public ingress; there is no private network to hide behind, so "unauthenticated" here means "public". What that would publish is cumulative spend, token volumes, request counts, and refusal rate — mildly embarrassing for a public demo, but the sharper problem is different: **an unauthenticated cost counter in front of a metered LLM API is a real-time feedback channel for anyone trying to burn the budget.** Cost-exhaustion is normally an attack you cannot observe; publishing the meter turns it into one with a progress bar. With no rate limiting in v1 (Key decision 14), that is a combination worth refusing. If the deployment later gains a private scrape path, this can be relaxed — the reason is the network topology, not the metric.
9. **`/metrics` reads in-process counters and never touches the database. Flagged.** The obvious implementation aggregates `query_log` — it is the authoritative ledger, it is correct across replicas, and the SQL is trivial. Rejected on two grounds. **(a) Connection budget:** SPEC-002 KD-8 leaves ten connections per replica and SPEC-004 spends two per query; a scrape every 15 s that takes one is monitoring competing with serving for the scarcest resource, and load correlated with *observing* the system is the worst kind. **(b) Cardinality of failure:** a slow aggregate over a growing `query_log` makes scrape latency grow with history, so the endpoint degrades exactly as the system gets more interesting. In-process counters are per-replica and reset on restart, which is **correct for Prometheus** — counters plus `sum()` across instances and `rate()`'s counter-reset detection handle both. **Division of labor, stated so it is not rediscovered:** `/metrics` answers "what is happening now, per replica"; `query_log` answers "what did this cost since the beginning", offline, where a slow query harms nobody.
10. **Concurrency is bounded above the pool by a derived semaphore, not by enlarging the pool. Flagged, arguing against the obvious choice — and this is a latent deadlock, not merely a capacity limit.** SPEC-004 opens **two** sessions per `retrieve()` and `asyncio.gather`s them. With `pool_size=5, max_overflow=5` (SPEC-002 KD-8 — a bound set by Azure's `max_connections`, not a preference), ten simultaneous requests can each acquire their *first* connection, exhausting the pool, and then all block awaiting a second that no one can release. **Every request then fails at `pool_timeout`, ~30 s later, under a load the arithmetic says should be fine.** The obvious fix — raise `pool_size` — is unavailable: the ceiling is external, and three replicas share it. So the bound is enforced where it can be reasoned about, as a semaphore on `/query`:

    ```
    MAX_CONCURRENT_QUERIES = (POOL_SIZE + POOL_MAX_OVERFLOW - RESERVED) // CONNECTIONS_PER_QUERY
                           = (5 + 5 - 2) // 2 = 4
    ```

    Requests beyond the bound wait briefly, then return **503 with `Retry-After`** — a fast, honest, retryable answer instead of a 30-second hang. `pool_timeout` drops to 5 s so that if the arithmetic is ever wrong, it says so quickly. **The constant is derived, never typed in**, and AC-8 asserts the derivation against the pool constants, so raising the pool without revisiting this fails a test rather than silently reintroducing the deadlock.

    **The semaphore is held only across `retrieve()`, not across the whole request.** It is a *pool* guard, not a request-rate limiter: retrieval's two connections are released before the provider call, and generation holds **zero** connections while awaiting the model. The multi-second part of a request therefore costs no pool at all, which is why a bound of 4 is far less restrictive than it first reads — the window it actually serializes is the ~13 ms retrieval phase (SPEC-004 AC-8).

    **What `RESERVED` reserves, enumerated so the margin is auditable rather than magic** *(added 2026-07-26, review amendment 3)*. The reserve exists for one purpose: **single-checkout consumers must never be starved by a saturated query load.** They cannot deadlock — a caller that takes exactly one connection and releases it always makes progress — so the reserve only has to cover *simultaneous* single-checkout demand, not a worst case. In code it is a named tuple whose length **is** the constant, so adding a consumer without adjusting the arithmetic is impossible:

    | Consumer | Connections | Reserved? | Why |
    |---|---|---|---|
    | `query_log` write | 1, brief | **yes** | Runs after retrieval releases, on every request. Losing it loses the cost record. |
    | `/health` readiness probe | 1, brief | **yes** | Scheduled and external; must answer under load, since that is when it is asked. |
    | `/metrics` scrape | **0** | n/a | In-process counters (Key decision 9). **The arithmetic depends on that decision** — making `/metrics` query the database would require `RESERVED = 3` and drop the bound to 3. |
    | Spend-ceiling refresh (Key decision 16) | 1, once per TTL per replica | no | One aggregate every 30 s against ~13 ms retrievals. Single-checkout, so the worst case is one query's brief wait. |
    | `/ingest` | 1, long-held | no | Admin-triggered, single-flight, and rare. An operator ingesting during peak demo traffic is a deliberate act whose worst case is a fast 503 on a query. **If it ever becomes routine, `RESERVED` becomes 3 and the bound drops to 3.** |
    | Alembic migrations | 1 | no | Run out-of-band via the CLI, never in the serving process. |

    **Rejected alternative — one SQL statement, no second connection, no deadlock class at all** *(recorded 2026-07-26, review amendment 2)*. Both branches and the fusion can be a **single statement**: a `dense` CTE (`ORDER BY embedding <=> :qvec LIMIT 50`), a `lexical` CTE (`ts_rank_cd … LIMIT 50`), `row_number()` over each, a `FULL OUTER JOIN` on `chunk_id`, and `1/(60 + dense_rank) + 1/(60 + lexical_rank)` computed in SQL. One connection per query, one round trip. **This does not bound the deadlock — it eliminates the class**, since a request that never holds two connections cannot half-allocate the pool, and `MAX_CONCURRENT_QUERIES` would rise from 4 to ~9. It is very likely *faster* too: one round trip instead of two, and no Python-side fusion. The concurrency SPEC-002 KD-5 buys is worth `max(vector_ms, fts_ms)` versus their sum — measured, about **7 ms against a 150 ms budget** (SPEC-004 AC-8). That is a real argument and it should be recorded as one, not strawmanned.

    **Why the semaphore wins for now**, in descending order of weight:
    1. **It would rewrite approved, implemented retrieval from inside the API spec.** SPEC-004's execution flow, its two-session design (KD-5), and four of its acceptance criteria (AC-2 fusion math, AC-3 hybrid mechanics, AC-7 concurrency, AC-11 `ef_search` scoping) all rest on the two-branch shape. An API spec amending retrieval's core is scope inversion; the semaphore is ~15 lines in this spec's own layer and is reversible in an afternoon.
    2. **Independent branch testability is the concrete loss.** `rrf_fuse` is today a pure function asserted against hand-built rank lists at exact `1/(60+r)` arithmetic, with no database. In SQL it becomes a query plan: a fusion bug turns into a SQL bug reproducible only against a live Postgres with a seeded corpus, and SPEC-004 AC-2 — the cheapest, sharpest test in the suite — has nothing left to assert.
    3. **The degenerate cases get fiddlier exactly where they are already subtle.** SPEC-004 AC-12 requires an empty full-text list to degrade to vector order rather than error; in a `FULL OUTER JOIN` that is a `COALESCE`-and-null-ordering question, and getting it wrong yields *plausible* results rather than an exception. The filter push-down (AC-10) must still be duplicated into both CTEs, so that complexity does not go away either.
    4. **The gain is currently unmeasurable.** The 4-query bound has never been reached, and the ~7 ms is 5 % of a budget with 10× headroom. Trading two tested seams for an unobserved win is the kind of change this project defers by policy (KD-1/KD-2/KD-8 of SPEC-004).

    **Honest statement of what is being deferred:** the semaphore *bounds* the failure, the CTE *removes* it. If anything ever adds a second concurrent connection consumer to a request, the arithmetic silently changes and the deadlock returns. **Revisit when** the concurrency bound becomes a real constraint on real traffic, or when any future spec touches fusion for its own reasons — at which point the rewrite is nearly free and should be taken.
11. **The app is built by a factory with an engine created in `lifespan`, and the current import-time wiring is a defect this spec fixes.** `main.py` today calls `_wire_ask()` at import: it reads environment variables, constructs an engine that is never disposed, and mounts `/ask` **only if** `DATABASE_URL` and `ANTHROPIC_API_KEY` happen to be set. That last behavior is the real problem — **a misconfigured deployment returns 404 on the endpoint**, which reads as "wrong URL" and sends the operator to look at routing rather than at configuration. `create_app(settings=…, retriever=…, generator=…)` builds the app; `lifespan` creates the engine on the running loop and disposes it on shutdown; a missing dependency is a **startup failure with a named cause**, never a route that quietly does not exist.
12. **`/ingest` is synchronous, admin-scoped, dry-run by default, and single-flight via a Postgres advisory lock. Flagged, arguing against the obvious choice — and the lock choice is the non-obvious part.** The obvious design for a minutes-long job is `202 Accepted` plus a job id and a status endpoint. Rejected for this system: it adds a job store, a status endpoint, and a background-task lifecycle to a service whose corpus is a handful of documents, and Container Apps may recycle a replica mid-job, so "background task" without durable job state is a promise the platform will not keep. Synchronous with a hard size bound is honest about what it is; over the bound, the answer is the CLI, which is where a real ingest belongs anyway. **The lock is a Postgres advisory lock rather than an in-process one**, because SPEC-002 KD-8 sizes for **three replicas** — an `asyncio.Lock` would serialize one replica while two others ingest the same documents concurrently, which is exactly the concurrent-replace race SPEC-003's idempotency contract does not cover. **Dry-run is the default** because the destructive, expensive call should be the one you have to ask for.
13. **`UnknownModelError` is a startup failure, not a request-time status. Flagged (minor).** SPEC-005 KD-10 raises it at client construction, so `create_app()` raises and the process exits with the message naming the module and the pricing page. The tempting alternative — construct the client lazily, serve until someone asks a question — turns a deployment error into a 500 per request, discovered by a user rather than by a deploy. A service that cannot price its own output should not report itself healthy.
14. **No rate limiting in v1, and this is the decision most likely to be wrong. Flagged.** The endpoint costs money per call and has none. Three things carry the weight instead: the admin key gates the only endpoint that spends *unbounded* amounts (`/ingest`), `max_tokens=4096` bounds any single answer, and Key decision 10's semaphore bounds concurrency (and therefore burn rate). What remains unbounded is *sustained* volume from a holder of the read key. Accepted because keys are not public and the deployment is a demonstration — and **Key decision 8 is load-bearing for this**: with no rate limit, publishing a live cost meter would hand an attacker the feedback signal they would otherwise lack. **Revisit when** the read key is shared beyond a small known set, the service is linked publicly, or `query_log` shows sustained volume from a single caller — at which point a per-key token bucket on `/query` is the smallest sufficient change, and `query_log` already holds the data to size it.
15. **Every response is a declared Pydantic model, and OpenAPI is asserted structurally rather than snapshotted. Flagged (minor).** `/ask` currently returns a bare `dict`, so its OpenAPI entry documents an untyped object — the document exists and says nothing, which is worse than absent because it looks like coverage. Declared response models fix that for free. **Snapshot-testing `openapi.json` is rejected:** it fails on every intentional change, so it trains people to regenerate it without reading the diff. AC-11 asserts properties that actually matter and cannot be satisfied vacuously — every 2xx has a non-empty schema, every non-public path declares the security scheme, no response schema is a bare object, and the SSE operation documents `text/event-stream`.

16. **A hard daily spend ceiling with a circuit breaker — chosen over per-IP limits and over cached-demo mode, and the degraded state is an honest 503 rather than a canned answer** *(added 2026-07-26, review amendment 1)*. Key decision 14 defers rate limiting; with a public demo URL in front of a metered API that is a live financial exposure, and the operational failure is worse than the financial one: **a drained quota takes the demo down precisely when someone is clicking it.** The three candidates, judged on what they actually bound:

    - **Per-IP limits** bound the *rate* of loss, not the loss. A thousand distinct IPs each politely under the limit still drain the quota, and IP is a weak identity — carrier NAT shares it among strangers, and an adversary rotates it for pennies. It is a useful burst limiter and a poor budget.
    - **Cached-answer demo mode** over a fixed question set bounds spend to zero, and is rejected on what it costs: the artifact stops being the system. A visitor who types their own question gets nothing, and a project whose entire thesis is *measured, real retrieval* would be shipping a recording of one. The demo's value is that it actually runs.
    - **A daily spend ceiling** bounds the quantity that is actually at risk — dollars — and is the only one of the three that does. **Chosen**, with a per-IP burst limit as a cheap complement so that one caller cannot consume the whole day's budget in a minute. The ceiling is the non-negotiable half; the burst limit is convenience.

    **The degraded response is `503 budget_exhausted` with `Retry-After` to the UTC reset, not a canned answer.** This departs from the option as posed, and the reason is Key decision 1's rule: `verdict` describes what happened to the *question*, and under a ceiling the question was never asked — so this is a transport-level condition, and a 5xx is the correct shape. It also needs no new `Verdict` value and therefore no SPEC-005 amendment. **A canned answer rendered in the same shape as a real one would be actively worse**: a viewer would read it as the system answering, which is the one impression this project should never create, and it would land in `query_log` as an answer that no model produced. A 503 that names the limit and the reset time reads as a deliberate guard, which is what it is.

    **Where enforcement lives, and why it lives in two places.** The application-level breaker is here: a spend accumulator, checked before the provider call, sourced from `query_log` (the authoritative ledger, per Key decision 9) and cached with a short TTL so the hot path does not pay a query per request. It is cached rather than exact because the ceiling must be **shared across replicas** — three replicas each enforcing $5 would enforce $15 — and the resulting overshoot is bounded and computable rather than hoped for: at most `spend_rate × TTL × replicas`, which at 30 s and observed per-query cost is cents.

    **Cross-spec note (binding on SPEC-010) — the provider-side cap is the backstop, and it is not optional.** An application-level breaker is code, and code has bugs; the failure it cannot protect against is itself. SPEC-010 must set a **provider-side monthly spend limit** on the Anthropic and OpenAI keys at or below the demo budget, plus an Azure budget alert, so that a breaker that fails open still cannot produce an unbounded bill. **Sequencing is binding: no public demo URL is published until both layers are live.** Publishing the URL first is precisely the ordering that turns a bug into an invoice.

    **The tradeoff against demo accessibility, stated plainly.** A ceiling means a visitor can arrive at a demo that is out of budget for the day — a real cost, paid to bound a worse one. Three things keep it small. Sizing: at roughly **$0.010 per query** at Sonnet 5's introductory rate (and ~$0.016 after 2026-09-01) — an estimate from the illustrative token counts above, to be replaced by the measured median from `query_log` before the URL is published — a **$5 daily ceiling is on the order of 300–480 questions per day**, far beyond plausible portfolio traffic. The per-IP burst limit stops one visitor from consuming that alone. And the 503 names the limit and the reset, so an arriving visitor sees a budget guard rather than an outage. **The failure mode this accepts** — an unlucky visitor on a busy day — is bounded, self-healing at midnight, and visible; the one it refuses is unbounded, silent until the invoice, and takes the demo down anyway.

## Acceptance criteria

- **AC-1 (JSON contract)** — `POST /query` with a valid key returns 200 whose body validates against `QueryResponse`: `verdict` is one of the five `Verdict` values; `citations[]` carry `marker`, `chunk_id`, **`section_path`**, `document_title`, `source_uri`, `doc_type`; `usage` carries `generator_identity`, both token counts, `cost_usd` **as a string**, `latency_ms`, `prompt_version`; `request_id` matches the `X-Request-ID` response header. `k` and `filters` reach `Retriever.retrieve` unchanged (asserted on a stub, including that `doc_types` and `document_ids` arrive as `RetrievalFilters`).
- **AC-2 (auth)** — No key → 401; unknown key → 401; read key on `/ingest` and on `/metrics` → **403**; admin key on `/query` → 200 (admin is a superset). Key comparison uses `hmac.compare_digest` (asserted by source inspection, as with SPEC-005 AC-3's sampling-parameter check). `/health`, `/healthz`, `/docs`, `/openapi.json` return 200 with no key. **Starting `create_app()` with neither key configured and without `RAG_QA_ALLOW_ANONYMOUS` raises**, naming the variable.
- **AC-3 (error mapping)** — Each row of the Interface table asserted end to end against stubs that raise the named exception: `EmbedderMismatchError` → 503 `embedder_mismatch` naming both identities; `EmptyCorpusError` → 503 `empty_corpus`; malformed body → 422 `validation_error`; `question: "   "` → 422; provider transport failure → 502 `upstream_error`. Every error body carries `error.code`, `error.message`, and `error.request_id`, and **no 5xx body contains a traceback, SQL, or a file path**.
- **AC-4 (verdicts are 200 — Key decision 1)** — Each of `insufficient_evidence` (model declined), `insufficient_evidence` (zero chunks retrieved, **no LLM call made**), `provider_refused` (`stop_reason: "refusal"`), `truncated` (`max_tokens`), and `error` (malformed verdict line) returns **HTTP 200** with the corresponding `verdict`, and each writes a `query_log` row. Asserted as a parametrized table so a future change cannot quietly reclassify one.
- **AC-5 (SSE framing and the verdict token — Key decision 4)** —
  - Response headers: `text/event-stream`, `no-cache`, `X-Accel-Buffering: no`. Every frame is `data: <json>\n\n` or a `:` comment; **no frame payload contains a raw newline**.
  - Event order: `verdict` first, `complete` last, `citation` frames at resolved markers.
  - **Byte-level, over every split point** of a provider stream that fragments the verdict line (`"ANSW"` / `"ERED\nProviders…"`, and the split after each character of both tokens): concatenating every `text` frame equals the non-streaming `answer` exactly, and **no frame contains `ANSWERED`, `INSUFFICIENT_EVIDENCE`, or any prefix of either**. Includes a body whose first word begins with the verdict's own first letter, since that is where a prefix-matching implementation false-positives.
  - A stream ending mid-verdict yields a `verdict` frame of `"error"` and **zero** `text` frames.
  - A stream idle longer than `RAG_QA_SSE_HEARTBEAT_SECONDS` emits a `:` comment frame.
  - A failure after the first frame emits a terminal `{"type":"error"}` frame and **does not** attempt to change the status code.
- **AC-6 (`query_log` per request)** — One completed `/query` writes exactly one row whose `provider`/`model` come from the client's identity, `retrieved_chunk_ids` equal the retrieved chunks in order, and `verdict`, `answer_text`, `prompt_version`, `latency_ms`, both token counts, and `cost_usd` are populated (SPEC-005 AC-8, now via HTTP). **A client disconnecting mid-stream still produces a row** with the tokens that were consumed — asserted by closing the response before `complete` arrives.
- **AC-7 (request id threaded through both libraries — Key decision 5)** — A request with no `X-Request-ID` gets a generated one, echoed on the response and present in the JSON body. A request supplying `X-Request-ID: abc-123` reuses it; one supplying a 500-character value or a value containing `\n` gets a **generated** id instead and still returns 200. Captured via `caplog`, **every** log record emitted during the request carries the same id — including the records SPEC-004's `Retriever` and SPEC-005's `Generator` emit, and **including both of SPEC-004's `asyncio.gather` branches**, which proves `ContextVar` propagation into tasks. Neither `Retriever.retrieve` nor `Generator.answer` gains a parameter — asserted on their signatures.
- **AC-8 (concurrency bound and the pool — Key decision 10)** — `MAX_CONCURRENT_QUERIES` **equals** `(POOL_SIZE + POOL_MAX_OVERFLOW - RESERVED) // CONNECTIONS_PER_QUERY` computed from `db.engine`'s constants, so changing the pool without revisiting this fails. `RESERVED` **equals `len(RESERVED_CONNECTIONS)`**, a tuple naming each reserved consumer — adding one without changing the arithmetic is impossible, and the margin is readable rather than magic. Against the real pooled engine: `MAX_CONCURRENT_QUERIES` simultaneous `/query` calls all succeed; the next one returns **503 with `Retry-After`** rather than blocking. **The deadlock this prevents is demonstrated, not asserted in the abstract:** with the semaphore disabled, `POOL_SIZE + POOL_MAX_OVERFLOW` simultaneous retrievals against a short `pool_timeout` fail with pool-timeout errors — the regression test for Key decision 10's premise.
- **AC-9 (`/ingest`)** — Read key → 403; admin key + `dry_run: true` → 200 with per-document chunk counts and estimated cost, **zero embedding calls and zero database writes**; `dry_run: false` → ingest performed, verdicts returned. A second concurrent ingest → **409 `ingest_in_progress`** (asserted by holding the advisory lock on a separate connection). A `paths` value escaping the corpus directory (`../`, absolute) → 422. A dry-run manifest exceeding `INGEST_MAX_CHUNKS` → 413 naming the CLI.
- **AC-10 (`/health` and `/healthz`)** — `/healthz` returns `200 {"status":"ok"}` **with no database configured at all** (SPEC-001 AC-2 still holds, and the container healthcheck cannot be made to depend on Postgres). `/health` returns 200 with every check `ok` against a live database; with the database unreachable it returns **503** `unavailable` naming `database`; with an empty corpus it returns 200 `degraded` naming `corpus`. The generator check performs **no** provider call (asserted: the fake client records zero calls).
- **AC-11 (OpenAPI — Key decision 15)** — `GET /openapi.json` returns a valid document in which: every 2xx response has a non-empty schema and **no response schema is a bare `object`**; `/query`, `/ingest`, and `/metrics` declare the `APIKeyHeader` security scheme and `/health`, `/healthz` do not; the `/query` operation documents **both** `application/json` and `text/event-stream`; and every `Verdict` value appears in the response schema's enum. `GET /docs` returns 200. No snapshot comparison.
- **AC-12 (`/metrics` — Key decisions 8, 9)** — Admin key required (401/403 asserted). Returns Prometheus text format exposing at minimum: request count by endpoint and status, `/query` latency histogram, verdict counts by verdict, token counters, and cumulative cost. **Serving a scrape opens no database connection** — asserted by counting checkouts on an instrumented pool across a scrape, which must be zero. Counters advance across requests within a process.
- **AC-14 (daily spend ceiling — Key decision 16)** *(added 2026-07-26, review amendment 1)* —
  - **Off by default:** with `RAG_QA_DAILY_BUDGET_USD` unset, `/query` serves normally and the guard opens no database connection at all.
  - **Breaker trips before the provider call:** with the ceiling set below the day's recorded spend, `/query` returns **503 `budget_exhausted`** with a `Retry-After` header, and the fake LLM client records **zero** calls — a breaker that trips after paying for the answer is not a breaker.
  - **Not a verdict:** the response is an error envelope, **not** a 200 with a canned answer, and it writes **no** `query_log` row. Asserted so nobody later "improves" it into a fake answer.
  - **Spend comes from `query_log`, scoped to the UTC day:** rows written before today's UTC midnight do not count toward the ceiling; rows written after it do. Asserted with rows on both sides of the boundary.
  - **Cached with a bounded overshoot:** repeated `/query` calls inside one TTL window issue **one** aggregate query, not one per request (asserted by counting statements); the in-process delta since the last refresh is added to the cached total, so the breaker trips within the same TTL window that crosses the limit rather than waiting for a refresh.
  - `Retry-After` is the whole seconds remaining until the next UTC midnight, and is > 0.
- **AC-13 (supersession is complete)** — `POST /ask` returns **404** (the route is gone, not aliased), `rag_qa.generation.api` no longer exists, and `rag_qa.main` contains no import-time engine construction — asserted by source inspection, since the defect in Key decision 11 is *when* the work happens, which no request can observe. SPEC-005 AC-11 is amended in the same commit.

## Test plan

`tests/test_api_query.py`, `test_api_sse.py`, `test_api_auth.py`, `test_api_ingest.py`, `test_api_health.py`, `test_api_metrics.py`, `test_api_openapi.py`, `test_api_concurrency.py` — async throughout, via `httpx.ASGITransport` against an app built by `create_app()`.

**Three tiers, and the tier boundary is decided by what the test actually needs — not by convenience.**

1. **No-database tier (the bulk).** `create_app()` with a stubbed `Retriever` and SPEC-005's `FakeLLMClient`, no `session_factory`. Backs AC-1–AC-5, AC-7, AC-10 (`/healthz`), AC-11, AC-13 — auth, error mapping, framing, schemas, and the verdict-token byte assertions. Runs in CI with no service dependency, which is where the majority of this spec's risk lives anyway.
2. **Pooled tier.** The app wired to SPEC-004's session-scoped `pooled_engine`. Backs AC-6, AC-8, AC-9, AC-12, and `/health`'s live checks.
3. **Lifespan tier.** Asserts `create_app()`'s startup/shutdown directly: engine constructed with SPEC-002 KD-8's bounds, disposed on shutdown, and the configuration failures of AC-2 and Key decision 13 raising at startup rather than per request.

**Binding fixture rule — the savepoint fixture cannot host API tests, and the reason must not be rediscovered.** SPEC-002's `connection`/`session` fixtures wrap a test in a single connection's rolled-back transaction. The app under test opens **its own** sessions from **its own** factory, and SPEC-004 needs two connections concurrently, so an API test on the savepoint fixture fails two ways at once: the app cannot see the fixture's uncommitted rows, and the app's own writes commit outside the rollback and leak. API tests therefore use SPEC-004's pattern — commit real rows through the pooled engine, clean up by **deleting exactly the ids inserted**, never `TRUNCATE`, so a misdirected `DATABASE_URL` still cannot destroy the corpus database. `query_log` rows are scoped by a per-test `uuid4` marker in `question` and deleted by it, matching SPEC-005's existing tests.

**Event-loop rule.** `asyncio_default_fixture_loop_scope = "session"` means the app's engine must be created **inside** the running loop, which is what moving construction into `lifespan` (Key decision 11) buys. Tests drive `lifespan` through `httpx.ASGITransport` rather than importing a module-level `app`, so an engine is never created at import time on no loop at all.

**Concurrency test (AC-8)** injects a delay into `vector_search`/`fulltext_search` (SPEC-004's AC-7 pattern) so the overlap window is deterministic rather than timing luck, issues `N+1` concurrent requests, and asserts N succeed and one is 503. The deadlock demonstration runs with the semaphore disabled and `pool_timeout` set to ~1 s so the regression test costs a second, not thirty.

**SSE tests** consume the raw byte stream and parse frames themselves rather than using an SSE client library — the framing *is* the thing under test, and a library that tolerates a malformed frame would hide the bug. The verdict-split cases are generated across every split point of the verdict line, as in SPEC-005 AC-7a.

**No network in any tier**, unchanged from SPEC-005: no provider call, no OpenAI embedding call. `/query`'s retriever is stubbed or backed by the seeded synthetic corpus with the stub embedder.

Tests are written from these acceptance criteria and committed with the implementation, in the same commit series referencing SPEC-006.
