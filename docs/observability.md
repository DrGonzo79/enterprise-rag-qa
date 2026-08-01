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
| `rag_qa_budget_pressure_total` | Is it refusing people because the headroom is *committed* rather than spent |
| `rag_qa_requests_shed_total` | Is the concurrency bound being reached |
| `rag_qa_budget_remaining_usd` | Headroom before it stops — money **spent**, not money claimed |
| `rag_qa_budget_reserved_usd` | Headroom **committed** to answers currently being generated |
| `rag_qa_budget_snapshot_age_seconds` | How old that headroom figure is |
| `rag_qa_telemetry_failures_total` | Completion records that could not be emitted |

Those exist because a budget trip, a shed, and an embedder mismatch were
otherwise all `status="503"` — three very different operational situations
counted as one number.

### Alerting on these

**An absent series is honest to a human and invisible to a threshold rule.**
`rag_qa_budget_remaining_usd < 1` never fires when the series is missing, and it
is missing in exactly the two cases worth paging about: a replica that has not
refreshed yet, and a deployment where the ceiling was never configured at all.
Every threshold below is paired with an `absent()` rule; the pair is the alert,
not the threshold alone.

```promql
# The demo has stopped answering. A trip, not a threshold -- this is the one
# that means visitors are seeing the explanatory state right now.
increase(rag_qa_budget_trips_total[15m]) > 0

# Headroom is nearly gone...
min by (ceiling) (rag_qa_budget_remaining_usd) < 1
# ...and, separately, there is no headroom figure at all. The rule above
# cannot see this case, which includes "no ceiling was ever configured".
absent(rag_qa_budget_remaining_usd)

# The headroom figure is too old to act on. 900s: the cache refreshes on any
# /query within RAG_QA_BUDGET_REFRESH_SECONDS (30s default), so anything past a
# quarter hour means this replica has been idle while others may have been
# spending the shared budget. Treat headroom as unknown, not as reassuring.
rag_qa_budget_snapshot_age_seconds > 900
absent(rag_qa_budget_snapshot_age_seconds)

# Requests are being refused because the remaining budget is committed to
# answers in flight, not because it is spent. A *share of arrivals*, not a raw
# rate: what matters is how many visitors were turned away, and 0.5/s is fine
# at 100 rps and an outage at 1 rps.
  rate(rag_qa_budget_pressure_total[15m])
/ rate(rag_qa_requests_total{endpoint="/query"}[15m]) > 0.05
# ...and, separately, the counter is not being reported at all. This pair works
# because the series is emitted from zero -- see below.
absent(rag_qa_budget_pressure_total)

# Committed headroom has caught up with remaining headroom: the next request is
# refused with budget_pressure. Two series rather than one, deliberately -- see
# below.
rag_qa_budget_reserved_usd >= min by (ceiling) (rag_qa_budget_remaining_usd)
absent(rag_qa_budget_reserved_usd)

# Telemetry itself is failing. Best-effort by construction (see below), so any
# non-zero rate is worth looking at rather than a threshold.
increase(rag_qa_telemetry_failures_total[15m]) > 0
```

**`remaining` means money spent; `reserved` means money claimed — and they are
two series on purpose.** Every request debits its worst-case cost before the
provider call and settles to the actual cost afterwards (SPEC-006 Key decision
16, amendment 5), which is what bounds a replica at `ceiling + one worst-case
query` regardless of how much traffic arrives. Folding reservations into
`rag_qa_budget_remaining_usd` would have silently changed what an existing
alert threshold on it means, so they are published side by side: subtract them
for committed headroom, read `remaining` alone for spend. **`reserved` is
expected to be jumpy** — it rises with every in-flight answer and falls as each
returns — so alert on the *relationship* between the two, never on `reserved`
crossing an absolute value.

**A `budget_pressure` refusal is not a budget trip, and
`rag_qa_budget_trips_total` deliberately does not count it.** A trip means the
demo is out of money until a UTC boundary; pressure means it is momentarily out
of *uncommitted* money and will answer again as soon as the current answers
finish. Paging on them together would cost the trip counter the one meaning it
has. **The rule for telling them apart, and for what to do about each:**

| | `budget_trips_total` | `budget_pressure_total` |
|---|---|---|
| What ran out | Money | Uncommitted money |
| Clears | At the UTC day or month boundary | When the answers in flight return — seconds |
| What the visitor sees | The explanatory panel, with a countdown | "Retry shortly", with no countdown |
| Any occurrence | Page. The demo is down until a boundary. | Expected under burst; not by itself a problem |
| Sustained | The budget is too small, or something is burning it | **The ceiling is now the admission control** — traffic has outgrown the budget, and every refusal is a visitor turned away |

**Why a counter and not just the gauges.** `rag_qa_budget_reserved_usd` rises and
falls with each in-flight answer, and a scrape every 15 s cannot observe a spike
that lasts three seconds. A deployment refusing a third of its arrivals to
reservation pressure therefore looks *identical* to a healthy one on the
headroom gauges. The counter is the only series that sees it, which is why the
refusal has one of its own rather than living only in
`rag_qa_errors_total{code="budget_pressure"}` — that series is real and is
counted, but it is label-created on first occurrence, so `absent()` on it means
"nothing has happened yet" and cannot be distinguished from "this replica has
stopped reporting". `rag_qa_budget_pressure_total` is emitted from process start
at zero precisely so that its `absent()` pair says something true.

**`rag_qa_telemetry_failures_total` is best-effort and is not the authoritative
count.** It is incremented inside the same `except` that reports the failure,
under `suppress`, on the `Metrics` object that may itself be what broke — so a
failure mode that takes out the counter takes out the count of itself. **The
`ERROR` record on `rag_qa.api.middleware` ("failed to record the completion of
…") is the floor**: alert on the log line and use the counter as the convenient
rate, never the reverse.

**The headroom figure is cached and never refreshed by a scrape.** `/metrics`
must open no database connection (Key decision 9), and the budget refresh is
deliberately outside the reserved-connection accounting, so a monitor polling
every 15 s would contend for exactly the connections the concurrency bound
protects. The snapshot therefore comes from whatever the last `/query` refreshed
— which on an idle replica ages without bound while other replicas keep spending
the shared budget. `rag_qa_budget_snapshot_age_seconds` is how you tell
"headroom is $4" from "headroom was $4, forty minutes ago". It is computed **at
scrape time** from the timestamp of the last refresh, so it advances between
scrapes with no traffic — an age frozen at refresh time would report the same
number forever and be worse than absent. Both series are
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
