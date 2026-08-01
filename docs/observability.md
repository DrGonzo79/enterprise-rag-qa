# Observability: one request, end to end

Every field and metric name in this document is asserted against what the code
actually emits (`tests/test_api_logging.py::test_observability_doc_matches_what_is_actually_emitted`).
If you rename a field and forget this page, the test fails. A walkthrough written
from memory names fields that are *nearly* right, and a reader who greps for one
and finds nothing concludes the logging is broken rather than the document.

## The shape of a record

Logs are one line of JSON on stderr (`LOG_FORMAT=json`, the default; `text` for a
human at a terminal). `json.dumps` escapes newlines, so a message containing one
cannot split a record across two lines — the same framing argument SPEC-006 Key
decision 3 made for SSE.

Every record carries the same five fields, and then whatever the call site
attached:

```json
{"ts": "...", "level": "INFO", "logger": "...", "msg": "...", "request_id": "..."}
```

`request_id` is empty outside any request. That is correct, not a bug: there is
no request (SPEC-006 Key decision 5).

## Following one `POST /query`

A caller may supply `X-Request-ID`; anything not matching
`^[A-Za-z0-9_.:-]{1,64}$` is silently replaced with a generated id, because the
header is attacker-controlled text headed for log records. The id is echoed on
the response — including on errors — and appears in the JSON body.

### 1. Retrieval

SPEC-004's `Retriever` emits one record per query, from inside the
`asyncio.gather` branches. It reaches this line with the request id attached and
without `Retriever.retrieve` ever gaining a parameter — that is what the
`ContextVar` in Key decision 5 buys.

```json
{"ts": "2026-07-26T17:04:02.980Z", "level": "INFO", "logger": "rag_qa.retrieval.service",
 "msg": "retrieve", "request_id": "9f2c1ab4e0d3477a", "query_sha": "a1b2c3d4e5f6",
 "k": 8, "result_count": 8, "distinct_section_rate": 0.75, "embed_ms": 84.2,
 "vector_ms": 7.41, "fts_ms": 5.02, "fuse_ms": 0.31, "total_ms": 97.6}
```

**The query itself is never logged** — `query_sha` is the first twelve hex
characters of its SHA-256. `query_log` holds the question and answer under an
explicitly demo-only retention decision; logs ship to a platform sink with its
own retention, its own copies, and no delete story. A question typed into a
public demo is not user input this project asked to keep twice.

### 2. Generation

SPEC-005's `Generator` logs only when something is worth knowing — a citation
marker outside the valid range, for instance. A healthy generation is silent
here and shows up in the completion record and the metrics instead.

### 3. Completion

One record per request, always, emitted after the response finishes — which for
a stream means after the last frame, so the verdict its background pump resolved
seconds later is present:

```json
{"ts": "2026-07-26T17:04:03.221Z", "level": "INFO", "logger": "rag_qa.api.request",
 "msg": "http.request", "request_id": "9f2c1ab4e0d3477a", "method": "POST",
 "route": "/query", "status": 200, "duration_ms": 1483.2, "verdict": "answered"}
```

`route` is the matched route template, never the raw path: an unmatched path
collapses to `__unmatched__`, because a label is safe only when its value space
is enumerable from the code, and a path is enumerable only after it matches
something.

`/health` and `/healthz` produce this record at `DEBUG`, so probes are counted
rather than narrated. Raise `LOG_LEVEL` to `DEBUG` to see them.

## When something goes wrong

A failed request produces the same completion record with `status` ≥ 400 and an
`error_code` naming the condition, at `WARNING` for 5xx:

```json
{"ts": "2026-07-26T17:04:03.221Z", "level": "WARNING", "logger": "rag_qa.api.request",
 "msg": "http.request", "request_id": "9f2c1ab4e0d3477a", "method": "POST",
 "route": "/query", "status": 503, "duration_ms": 4.1, "error_code": "budget_exhausted"}
```

Every `error_code` has an entry in `rag_qa/api/conditions.py` carrying its
`presentation` and `reset`, both of which ship in the error envelope so a
frontend renders the condition without keeping its own copy of the list.
`reset: operator` means no countdown exists and none must be rendered —
`embedder_mismatch`, `empty_corpus`, and `misconfigured` clear when someone acts,
not at midnight.

### The spend ceiling

A budget trip logs the figures and tells the caller none of them:

```json
{"ts": "2026-07-26T17:04:03.219Z", "level": "WARNING", "logger": "rag_qa.api.budget",
 "msg": "spend ceiling reached", "request_id": "9f2c1ab4e0d3477a", "ceiling": "daily",
 "limit_usd": "0.64", "spent_usd": "0.652100", "origin": "configured",
 "resets_at": "2026-07-27T00:00:00+00:00"}
```

The response says the demo's spending limit for this window has been reached and
when it resets, and names no amount. An error body carrying the ceiling and the
running total is a live progress bar for anyone trying to drain the budget, and a
side channel around the admin scope on `/metrics`.

### A stream that fails after it started

The client has already received a `200`, so the failure is delivered in-band as a
terminal frame — and recorded here, which it previously was not:

```json
{"ts": "2026-07-26T17:04:05.100Z", "level": "ERROR", "logger": "rag_qa.api.routes.query",
 "msg": "stream failed after the response began", "request_id": "9f2c1ab4e0d3477a",
 "error_code": "upstream_error", "exception_type": "APIConnectionError"}
```

A client that simply disconnects produces no such record. A disconnect is not a
provider failure, and conflating them would make this record meaningless on
exactly the deployment where clients wander off.

## What `/metrics` answers

Admin key required — the endpoint publishes spend, and an unauthenticated cost
counter in front of a metered API is a real-time feedback channel for anyone
trying to burn the budget (SPEC-006 Key decision 8). Counters are per-replica and
in-process; `query_log` is the authoritative ledger, offline.

| Series | Answers |
|---|---|
| `rag_qa_requests_total` | Traffic, by route template and status |
| `rag_qa_query_latency_seconds` | End-to-end `/query` distribution |
| `rag_qa_verdicts_total` | How often the model answers, declines, or is truncated |
| `rag_qa_prompt_tokens_total`, `rag_qa_completion_tokens_total` | Token volume |
| `rag_qa_cost_usd_total` | Spend since process start |
| `rag_qa_errors_total` | **Which** failure, not merely which status |
| `rag_qa_budget_trips_total` | Has the demo stopped answering, and which ceiling stopped it |
| `rag_qa_requests_shed_total` | Is the concurrency bound being reached |
| `rag_qa_budget_remaining_usd` | Headroom before it stops |
| `rag_qa_budget_snapshot_age_seconds` | How old that headroom figure is |
| `rag_qa_telemetry_failures_total` | Completion records that could not be emitted |

Those exist because a budget trip, a shed, and an embedder mismatch were
otherwise all `status="503"` — three very different operational situations
counted as one number.

**The headroom figure is cached and never refreshed by a scrape.** `/metrics`
must open no database connection (Key decision 9), and the budget refresh is
deliberately outside the reserved-connection accounting, so a monitor polling
every 15 s would contend for exactly the connections the concurrency bound
protects. The snapshot therefore comes from whatever the last `/query` refreshed
— which on an idle replica ages without bound while other replicas keep spending
the shared budget. `rag_qa_budget_snapshot_age_seconds` is how you tell
"headroom is $4" from "headroom was $4, forty minutes ago". Both series are
**absent** until the first refresh: a fresh replica's totals are zero, and
publishing the full ceiling as headroom would announce plenty of budget at the
moment the process knows least.

## Joining it up

```
grep '"request_id": "9f2c1ab4e0d3477a"' | jq -c '{logger, msg, status, duration_ms}'
```

One id spans the retrieval record, any generation warning, the budget record if
the ceiling tripped, and the completion record — with no library signature
mentioning HTTP anywhere.
