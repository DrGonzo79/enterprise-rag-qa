# SPEC-008 — Request Records and Failure Signal

**Status:** Approved — 2026-07-26, implemented in the same commit series.

**Review amendment (2026-07-26, approving round):** the server-side taxonomy is not defined here. It is derived from `rag_qa/api/conditions.py`, the shared registry introduced by SPEC-006 Key decision 16's reconciliation (commit `e81d569`) — see Key decision 9. The review that approved this spec required it: *"that taxonomy and the client-facing states should not be two independent lists."*
**Depends on:** SPEC-006 (API — the request-id seam, log configuration, in-process counters, `/metrics`)
**Hands off to:** SPEC-010 (deployment — log shipping, retention, alert rules, budget alerts)

**Scope note.** An earlier draft of this spec was titled *Observability* and also covered log configuration, `LOG_LEVEL`, and metric label cardinality. Those were not gaps in coverage — they were **defects in shipped code**, and they were fixed under SPEC-006 as Key decision 17 in commit `424b667`. Parking a bug in an unimplemented spec files it as roadmap, which is the opposite of what a defect needs. What remains here is genuinely new work, and the spec is named for it.

## Purpose

SPEC-006 delivers a request id that reaches an operator, structured records from retrieval and generation, and counters behind `/metrics`. Three things are still missing, and they share a shape: **the system's most important moments produce the least signal.**

1. **No record says what happened to the request.** The id correlates SPEC-004's and SPEC-005's records with each other, but nothing states the outcome — method, route, status, duration. A trace has links and no spine.
2. **The three ways this service refuses work are indistinguishable.** A budget trip, a shed under the concurrency bound, and an embedder mismatch all render as `rag_qa_requests_total{route="/query",status="503"}`. The single most consequential operational state of this deployment — *the demo has stopped answering and will not resume until the window resets* — cannot be read from the endpoint an operator reads.
3. **A provider failure mid-stream leaves no server-side record at all.** `_pump` catches the exception, queues it, and the client receives a terminal error frame. Nothing is logged. The one failure mode that reaches a user as a broken answer is the one the server keeps no evidence of.

Plus the artifact that makes the rest usable by someone who did not write it: `docs/observability.md`, one real request followed end to end, with its field names checked against the code so it cannot quietly go stale.

## Non-goals

- **Log configuration, formatters, `LOG_LEVEL`, and metric label bounds** — fixed under SPEC-006 Key decision 17 (commit `424b667`). This spec consumes that configuration and does not revisit it.
- **OpenTelemetry traces, spans, and exporters** — first on the charter's scope-cut ladder. Key decision 8 states the seam and the revisit trigger; nothing is built.
- **Dashboards, alert rules, scrape configuration, log shipping, retention** — SPEC-010. This spec produces signal; deployment decides who is woken by it.
- **Changing SPEC-006's request-id design.** No library signature gains a parameter.
- **Per-caller cost attribution, usage billing, multi-tenancy** — unchanged non-goals from SPEC-005 and SPEC-006.
- **Evaluation metrics** — SPEC-007 measures answer quality offline. `/metrics` answers "what is happening now".
- **Log sampling or volume control**, beyond the probe-endpoint rule in Key decision 1. At demo traffic a day of logs is a few megabytes; sampling would tune a problem that does not exist and would complicate the correlation this spec exists to provide.
- **A new runtime dependency.** Key decision 6 adds a test-only one.

## Interface

### Modules

```
src/rag_qa/api/
    middleware.py         # + the completion record, in RequestContextMiddleware (KD-9)
    metrics.py            # + breaker/shed/error counters, budget headroom gauge
    budget.py             # + the ceiling that tripped, so the counter can be labelled
    conditions.py         # the shared registry (SPEC-006 KD-16) this taxonomy derives from
    context.py            # + the outcome ContextVar the record is assembled in
    routes/query.py       # + server-side ERROR record when a stream fails mid-flight
docs/
    observability.md      # one real request, end to end
```

### The completion record

Emitted once per HTTP request, at `INFO`, or `WARNING` for 5xx:

```json
{"ts":"2026-07-26T17:04:03.221Z","level":"INFO","logger":"rag_qa.api.request",
 "msg":"http.request","request_id":"9f2c1ab4e0d3477a","method":"POST","route":"/query",
 "status":200,"duration_ms":1483.2,"verdict":"answered"}
```

| Field | Always | Notes |
|---|---|---|
| `method`, `route`, `status`, `duration_ms` | yes | `route` is the matched template from SPEC-006 Key decision 17, never the raw path |
| `verdict` | no | present only when the handler produced one |
| `error_code` | no | the error envelope's code, when the response was an error |

### Metrics added

| Series | Type | Labels | Answers |
|---|---|---|---|
| `rag_qa_budget_trips_total` | counter | `ceiling` = `daily`\|`monthly` | Has the demo stopped answering, and which ceiling stopped it |
| `rag_qa_requests_shed_total` | counter | — | Is the concurrency bound being reached |
| `rag_qa_errors_total` | counter | `code` (the envelope's closed set) | Which failure, not merely which status |
| `rag_qa_budget_remaining_usd` | gauge | `ceiling` | How much headroom is left |

### Configuration

None added.

### New dependencies

None at runtime. One test-only: `prometheus-client`, used solely as a parser (Key decision 6).

## Key decisions

1. **One completion record per request, emitted by the middleware — not uvicorn's access log. Flagged, arguing against the obvious choice.** Uvicorn already logs every request, for free, in a familiar format. Rejected on three counts, the first decisive: **it does not carry the request id**, so the one line naming the request's outcome would be the only line in the trace that cannot be joined to the rest of it. Second, it is a preformatted string, so a JSON pipeline gets prose where it needs `status` and `duration_ms` as values. Third, its timing brackets the ASGI call and excludes middleware — where the shed and budget decisions are made, so the request whose duration matters most is the one it measures worst. `configure_logging()` therefore **disables uvicorn's access log**, because two access logs per request is worse than either alone. The record is emitted in a `finally`, so a shed 503, a budget 503, and an unhandled 500 each produce exactly one: **a request that failed early is not a request that goes unrecorded.**

    **Probe endpoints are the immediate consequence, and they are handled rather than discovered.** A readiness probe every 10 s across three replicas is ~26,000 records a day whose content is "still ok" — volume that costs money to ship, buries what matters, and trains an operator to filter out the logger that would have told them the corpus went empty. `/health` and `/healthz` completion records are emitted at `DEBUG`, so they vanish at the default level while still being counted in `/metrics`, where a count is exactly the right representation for a high-frequency uninteresting event.

2. **Replacing uvicorn's access log removes no coverage, and the residual gap is stated rather than assumed** *(added 2026-07-27, post-implementation review)*. Disabling an access log is the kind of change whose cost shows up only when something breaks, so the question was checked against uvicorn's source rather than reasoned about. `uvicorn.access` is written from **one place** — `RequestResponseCycle.send`, at `http.response.start` — so it covered exactly the requests that reached the ASGI app and produced a response. Those are precisely the requests the completion record now covers, with the request id, the status as a value, and a duration measured around the middleware. **Nothing that was recorded before is unrecorded now.**

    **What was never covered by either, and still is not.** A request uvicorn rejects at the protocol layer — a malformed request line, a header h11 refuses — never reaches the ASGI app, so it never reached the access log either. It is logged by `uvicorn.error` ("Invalid HTTP request received.") and answered with a 400 that no middleware sees. That logger is **kept and routed through this formatter**, so one pipeline carries both rather than two formats. Such a record correctly has an empty `request_id`: there is no request context, because there was never a valid request.

    **The gap that remains, stated plainly:** a connection that fails before or during the request line produces no completion record, no route label, and no duration — only a `uvicorn.error` warning. That is not fixable from inside the ASGI app, because the app is never invoked. **And it is not fixed by re-enabling the access log**, which is worth saying explicitly: that line embeds `get_path_with_query_string(scope)` — the raw, attacker-controlled path and query — which is the same unbounded-attacker-content problem as the 404 label cardinality bug (SPEC-006 Key decision 15), moved from a metric label into a log line. Bounded silence beats unbounded noise.

3. **The completion record cannot distinguish a client disconnect from a delivered response, and does not pretend to** *(added 2026-07-27, post-implementation review)*. The record fires on a disconnect — it is emitted in a `finally` — carrying `status: 200` and the verdict, which is exactly what it carries for a stream the client read to the end. That is not an oversight to be patched with a `client_disconnected` field, because **the server genuinely does not know**: generation deliberately outlives the connection (the tokens were spent whether or not anyone was listening), and a disconnect arriving after the final frame is indistinguishable from an orderly close. A field asserting otherwise would be a signal that guesses, and a guessing signal is worse than an absent one — it gets trusted. What *is* distinguishable is a provider failure, via the separate `ERROR` record from the pump (Key decision 8); the completion record reports what the server did, and the pump record reports whether it worked.

4. **The completion record carries no question text, no answer text, and no key. Flagged — the easiest rule here to violate later with one temporary debug line.** SPEC-004 already logs `query_sha` rather than the query, and SPEC-005 stores question and answer in `query_log` under an explicitly demo-only retention decision. That distinction becomes the rule, because **logs and `query_log` are not the same kind of store**: the table is one `DELETE` from being purged and lives in a database the owner controls, while log records ship to a platform sink with its own retention, its own copies, and no delete story any spec here can promise. A question typed into a public demo is user input the project never asked to keep twice. AC-2 asserts it with a canary rather than trusting review, because the violating change is always one line and always looks harmless.

    **`query_sha` is a correlation key, not a confidentiality boundary — stated rather than salted, and here is why** *(added 2026-08-01)*. It is an unsalted SHA-256 prefix, and a demo's question space is small enough to enumerate, so anyone holding the logs can recover the questions by dictionary. Salting it with a deployment secret was considered and **rejected**, because it would buy a guarantee this system does not actually make: `query_log` holds the same questions in **plaintext**, in the same deployment, by an explicit SPEC-005 decision. A salted hash beside a plaintext column is security theatre — it would read as a boundary to the next person while the boundary does not exist, which is the "looks protected while being open" failure Key decision 7 of SPEC-006 refuses elsewhere. It would also add a secret that must be identical across replicas and stable across restarts or correlation silently breaks, and one more value that must never be logged by the logging module.

    **What the hash is actually for:** counting repeated questions and joining a slow retrieval to the query that caused it, without putting user text in a second store with its own retention and its own copies. That is a *retention* argument, not a secrecy one, and Key decision 4 should be read as making only that claim. **Revisit and salt when** either half of the premise changes: if `query_log` stops holding question text, or if logs are shipped to a sink with a materially different trust boundary than the database — at which point the hash becomes the only copy and its reversibility starts to matter.

5. **The three refusals get distinct counters, and `/metrics` stays admin-scoped precisely because of what they say.** Status alone cannot tell a budget trip from a shed from an embedder mismatch, so today the endpoint an operator reads cannot answer the question they are actually asking. Adding `ceiling`-labelled trip counters and a headroom gauge fixes that. **The tension with SPEC-006 Key decision 8 is real and resolves the same way it did there:** budget headroom is the sharpest possible version of "a real-time feedback channel for anyone trying to burn the budget" — it is a progress bar with the finish line labelled. It is acceptable **only** behind the admin key, and this decision stands as an argument against ever relaxing that scope: the endpoint just got more sensitive, not less.

6. **No `prometheus_client` at runtime; it is added as a test-only parser instead. Flagged (minor), arguing against the obvious choice.** The obvious move is to adopt the library and delete the hand-rolled renderer. Rejected for now: the renderer is 77 lines inside a 166-line module, written and tested (it was ~90 lines of module before this spec's series; the growth is real, which is why the revisit triggers below are numbers rather than a feeling), and the exposition format is stable text rather than a moving target; the library brings a process-global registry that fights the per-app `Metrics` instance SPEC-006 Key decision 9 deliberately uses, and its multiprocess mode is a real complication for a Container App. **What is not acceptable is hand-rolling a wire format and hoping it parses**, so the library becomes a development dependency and AC-6 feeds `/metrics` through its parser. The oracle is the library; the runtime is ours. **Revisit when** any one of these trips — numbers, so it can actually fire rather than being a matter of opinion *(sharpened 2026-08-01; "the argument weakens as it grows" was not a trigger)*:

    - **`render()` exceeds 150 lines**, or `metrics.py` exceeds 250. Measured 2026-08-01: **77** and **166**. The series this spec added cost roughly 55 module lines, so about one and a half more features of that size trips it.
    - **More than 16 metric families**, or **any family with more than one label dimension**. There are **12** today and every one is zero- or one-dimensional; two dimensions is where hand-rolled escaping and label ordering stop being obviously correct by inspection.
    - **A named feature that forces it, whichever comes first:** a second histogram (bucket rendering is the part most likely to be subtly wrong twice), exemplars, or `multiprocess` mode — the last being a hard switch, since the library's registry is the only sane way to aggregate across workers.

    Any one of those is sufficient. The point of naming three is that the first two are countable in CI and the third is a design event nobody can miss.

7. **A stream that fails mid-flight is recorded server-side, at `ERROR`, in addition to the frame the client receives. Flagged (defect-shaped, but new behaviour rather than a fix).** `_pump` currently catches the provider exception, queues it, and `frames()` yields a terminal error frame — a correct client-facing outcome with no server-side trace whatsoever. The asymmetry is backwards: **this is the only failure mode that reaches a user as a broken answer, and it is the only one that leaves the operator nothing to look at.** `query_log` does not close the gap either — it records the tokens consumed, not that the stream failed. The record carries the request id and the translated error code, which is what joins it to the completion record and to SPEC-004's and SPEC-005's lines. It is emitted where the exception is caught, before the frame is queued, because the frame's delivery depends on a client that may already be gone.

8. **OpenTelemetry is scoped and declared, not built. Flagged (deliberate deferral).** The charter puts OTel traces first on the cut ladder and this spec agrees rather than quietly reinstating them. What a trace buys over what is specified here is span-level attribution *across services*, and there is one service. The three latency questions this deployment has — embedding round-trip, retrieval, provider call — are already answered by SPEC-004's branch timings, the completion record's total, and the histogram's distribution. **The seam, stated so adopting OTel later is wiring rather than redesign:** the request id is already a `ContextVar` propagated into tasks, which is the mechanism a span context uses, and the completion record already holds the attributes a root span would. **Revisit when** a second service appears, or when a latency question arises that the histogram plus branch timings genuinely cannot answer — not when a trace would merely look impressive.

9. **The failure signal is labelled from the shared condition registry, not from a list of its own. Flagged — this is the decision that keeps the spec honest.** The obvious implementation of "distinguish a budget trip from a shed from an embedder mismatch" is a small enum here, next to the counters. Rejected: SPEC-006 Key decision 16 already needs a client-facing list of the same conditions, and two lists of the same set drift in the worst possible way — a new failure mode is added to whichever half its author was looking at, and the other half silently renders it as a generic error or counts it as one. Neither half breaks; both quietly become wrong. `Metrics.observe_error` therefore resolves the code through `spec_for()` and **raises** on a code with no registry entry, which means a condition cannot be counted server-side without also having a rendering. AC-9 asserts both directions, because the reverse — a rendering for something nothing can produce — is a frontend branch that can never be reviewed against reality.

    **The completion record is emitted from `RequestContextMiddleware` rather than a middleware of its own, and the reason is ordering.** It must run *inside* the request-id context, or the one line naming the outcome is the only line in the trace that cannot be joined to the rest; and *after* the error envelope has been chosen, or an unhandled exception is recorded before anyone has decided it is a 500 with a code. A separate middleware sits on one side of that boundary or the other. There is no position that is both, so the two concerns share a class and the docstring says why.

10. **`docs/observability.md` traces one real request, and its field names are asserted against the code. Flagged — the alternative is a document worse than nothing.** A walkthrough written from memory names fields that are nearly right, and a reader who greps for one and finds nothing concludes the logging is broken rather than the doc. The sample records and metric names are produced by **running** the request, and AC-8 parses the document, extracts every field and metric name it shows, and asserts each is actually emitted. **The doc drifts, the test fails, someone fixes the doc** — the only mechanism that has ever kept documentation true. It is also the artifact a reader of a public repository is most likely to judge this project by, which is the second reason it is in scope rather than deferred.

## Acceptance criteria

- **AC-1 (the completion record exists and is singular)** — Exactly one `msg: "http.request"` record per request, carrying `method`, `route`, `status`, `duration_ms`, and `request_id`, asserted for each of: a 200 `/query`, a 503 shed by the semaphore, a 503 `budget_exhausted`, an unhandled 500, and a completed SSE stream (emitted when the stream ends, not when headers are sent). `duration_ms` > 0 in every case; `route` is the matched template, never a raw path; 5xx records are at `WARNING` or above; the `/query` record carries `verdict` and an error response carries `error_code`.
- **AC-2 (the record carries nothing sensitive)** — A `/query` whose question contains a canary and whose fake-client answer contains a second canary emits **no** record containing either canary, the read key, or the admin key. Asserted over every record emitted during the request, in both `json` and `text` modes.
- **AC-3 (probe endpoints do not flood)** — Ten `/health` and ten `/healthz` calls at the default level emit **zero** completion records, while `rag_qa_requests_total` for those routes advances by ten each. At `LOG_LEVEL=DEBUG` the same calls emit twenty. A count is the right representation for a high-frequency uninteresting event; a line per poll is not.
- **AC-4 (the counters advance, and only on their own trigger)** — `rag_qa_budget_trips_total{ceiling="daily"}` and `{ceiling="monthly"}` each advance on the corresponding trip and on nothing else — including not on each other, asserted with a case where both ceilings are exhausted and the monthly is reported (SPEC-006 KD-16). `rag_qa_requests_shed_total` advances on a semaphore shed and **not** on a budget 503. `rag_qa_errors_total{code=…}` advances with the envelope's code for `embedder_mismatch`, `budget_exhausted`, `upstream_error`, and `validation_error`. `rag_qa_budget_remaining_usd` equals the ceiling minus recorded spend and is absent when no ceiling is configured.
- **AC-5 (the three refusals are distinguishable)** — After one budget trip, one shed, and one embedder mismatch, `/metrics` output contains three distinct series that separate them — the failing assertion being that all three are visible as themselves rather than as `status="503"` counted three times. This is the criterion the whole metric section exists for; it is asserted directly rather than inferred from the individual counters.
- **AC-6 (the exposition is valid, judged by something that is not us)** — `/metrics` parses without error via `prometheus_client.parser.text_string_to_metric_families` (test-only dependency), every family carries `HELP` and `TYPE`, and no metric name appears twice. Asserted after traffic that populates every series, so the parser sees labels and buckets rather than an empty registry.
- **AC-7a (a disconnect is recorded and not guessed at — Key decision 3)** *(added 2026-07-27)* — A client that reads one frame and leaves still produces **exactly one** completion record, with `status: 200` and the verdict the pump resolved. It carries **no** `client_disconnected` field and produces no `ERROR` record: the assertion is the absence, so a later change that adds a guessing signal fails here.
- **AC-7 (a stream failing mid-flight is recorded)** — An SSE stream whose provider fails after the first frame emits an `ERROR` record carrying the request id and the translated error code, **in addition to** the terminal error frame the client receives, and still produces exactly one completion record. Asserted by counting records, since the current behaviour produces zero. A client that disconnects mid-stream — which is not a failure — produces **no** `ERROR` record, so the two are not conflated.
- **AC-8 (the walkthrough cannot go stale)** — `docs/observability.md` exists; every JSON field name in its sample records is emitted by the formatter **with a non-empty value in at least one record** — presence alone would let a field that is always `""` satisfy a check meaning to prove it is populated, which `request_id` on a `uvicorn.error` record is exactly *(tightened 2026-08-01)* — and every metric name it mentions appears in `/metrics` output. **The sample driven for this must cover every record the document shows** — a real retrieval, a completion, a budget trip, and a stream that dies mid-flight — because a narrower sample lets a renamed field in an infrequent record slip through, which is the exact drift being guarded. Renaming a field without updating the document fails this test.
- **AC-12 (the headroom gauge is cache-only and says how stale it is)** *(added 2026-08-01)* — `rag_qa_budget_remaining_usd` and `rag_qa_budget_snapshot_age_seconds` are **absent** until the guard has refreshed at least once, so a fresh replica never publishes an unspent ceiling as headroom. Once present, the age advances with the cache rather than with the scrape. A scrape of a budget-configured app with a populated cache still opens **zero** connections (SPEC-006 AC-12), and making the snapshot path query fails that.
- **AC-13 (a telemetry failure is visible and harmless)** *(added 2026-08-01)* — With the completion path made to raise unconditionally, the response is unaffected — status, body, and verdict intact — while the failure is reported on `rag_qa.api.middleware` at `ERROR` with a structured `error.type`, **not** on `rag_qa.api.request`, and `rag_qa_telemetry_failures_total` advances. Asserted together, because a report emitted through the machinery that is failing is not a report.
- **AC-10 (failure paths cannot take the response with them)** *(added 2026-07-27)* — A handler raising a bare, untranslated exception returns a well-formed 500 carrying `internal_error`, the request-id header, and `presentation: degraded`, with no exception type or traceback in the body, and produces **exactly one** completion record. An `ApiError` assigned an unregistered code at runtime — the one path no class check covers — degrades to the same 500 rather than escaping. A `Metrics` call that raises inside the completion path is swallowed and logged, and the response is unaffected: asserted by replacing `observe_error` with one that raises and checking the caller still receives its 422.
- **AC-11 (the uvicorn seam)** *(added 2026-07-27)* — `configure_logging()` disables `uvicorn.access` and **keeps** `uvicorn.error`, routing it through this formatter: a record emitted on that logger appears as one JSON line with an empty `request_id`, which is correct because the request never reached the app. Silencing the wrong logger fails this.
- **AC-9 (one registry, two sides)** — Every code the failure signal can count has a `CONDITIONS` entry and every entry is reachable from a raisable error, asserted in both directions. `Metrics.observe_error` **raises** on an unregistered code, and defining an `ApiError` subclass with one raises at class creation. The `code` labels appearing in `rag_qa_errors_total` are a subset of the registry. Adding a failure mode to one half only is therefore an import-time or test-time failure rather than something a reviewer must catch.

## Test plan

`tests/test_api_logging.py` (AC-1 – AC-8) and `tests/test_api_conditions.py` (AC-9).

**Capture is through the configured handler, never `caplog`.** SPEC-006's `captured_logs` helper (`tests/test_api_context.py`) installs the real formatter over a `StringIO` and is reused here. `caplog` reads `LogRecord` objects before formatting, which is precisely how SPEC-006's original AC-7 passed while no formatter existed — the fifth entry in CLAUDE.md's list of tests that proved nothing. Records are parsed with `json.loads` per line, so a record that fails to be one line fails loudly.

**Tiering follows SPEC-006's.** AC-1, AC-2, AC-3, AC-7 run on the no-database tier with a stubbed retriever and `FakeLLMClient`. AC-4, AC-5, AC-6 need the pooled tier for the budget path. AC-8 needs whatever tier produces the sample it checks.

**No network in any tier**, unchanged from SPEC-005 and SPEC-006.

**Every test is verified by breaking the behaviour it covers** (CLAUDE.md rule 3). Ten breaks were run across the implementation and the post-implementation review, and all ten were caught: removing the `finally` that emits the completion record (fails AC-1 for the shed and 500 cases, not merely the 200 case); labelling both budget ceilings with one constant (AC-4); demoting the mid-stream `ERROR` record below the default level (AC-7); narrating probes at `INFO` again (AC-3); renaming a field in the document only (AC-8); letting `observe_error` accept an unregistered code (AC-9); removing the `try` that contains a telemetry failure (AC-10); silencing `uvicorn.error` instead of `uvicorn.access`, and leaving `uvicorn.error` unrouted (AC-11); and dropping the unknown-member fallback from the published schema (SPEC-006 AC-19).

Tests are written from these acceptance criteria and committed with the implementation, in the same commit series referencing SPEC-008.
