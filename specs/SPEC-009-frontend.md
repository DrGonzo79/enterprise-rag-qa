# SPEC-009 — Frontend

**Status:** Draft — awaiting review by the repository owner

**Date:** 2026-08-01
**Depends on:** SPEC-006 (HTTP API), SPEC-007 (eval harness — not yet written), SPEC-008 (request records)

**Drafting note, so the review knows what it is reading.** This draft is
assembled from what the repository *binds*: SPEC-006 Key decision 16's
cross-spec note (which is binding on this spec), the condition registry in
`rag_qa/api/conditions.py`, the SSE contract in SPEC-006's Interface, and
CLAUDE.md's stack and scope-cut ladder. Where a decision was not already bound,
it is proposed here and flagged.

**Approval is blocked on two things outside this spec, and neither is a
detail.** Read these before the Key decisions:

1. **Where the read key lives (SPEC-010).** A browser cannot keep a secret, so a
   page that calls `/query` directly ships the key in JavaScript — which makes
   SPEC-006 Key decision 14's acceptance argument void and takes Key decision
   8's with it. SPEC-006 Key decision 14 is corrected as of 2026-08-02 and hands
   the mechanism — a same-origin proxy plus a per-IP burst limit — to SPEC-010,
   because where a key lives is deployment topology. **This spec cannot be
   approved until SPEC-010 settles it**, since a frontend built against one of
   those answers is not trivially portable to another. See Key decision 8.
2. **A terminal SSE error frame carries no rendering (SPEC-006, proposed).** See
   Key decision 4 and the *Blocked* note under it: the wire contract this spec
   is supposed to read its renderings off does not currently carry them on the
   one path where the status code is already gone.

**Three further dependencies are named rather than assumed**, and all are on
things that do not exist yet: the eval report (SPEC-007, unwritten), the
recorded Q&A fixture (needs a corpus and a real model run), and the deployment
that serves the static assets (SPEC-010).

## Purpose

Make the system evaluable by a person with a browser.

Everything below the API is already testable by machine and invisible to a
reader. The frontend exists to do three things, in this order of importance:

1. **Show a real answer with real citations** — the retrieval and citation
   design is the project's actual claim, and a marker that resolves to
   "EU AI Act › CHAPTER III › Article 16" is the claim being demonstrated.
2. **Render a refusal as a correct outcome.** SPEC-006 Key decision 1 makes
   `insufficient_evidence` a **200**, and the eval set scores declining. A UI
   that renders it as a failure would contradict the thesis of the project in
   the one place a visitor looks.
3. **Render the deliberate not-answering states honestly** — the budget guard,
   the concurrency shed, the misconfigured deployment — so that a visitor who
   arrives on an exhausted day can still evaluate the work, and never sees a
   bare 503 that reads as "this project is broken".

## Non-goals

- **No conversation.** One question, one answer. Multi-turn needs history,
  context management, and a different eval story; none of that is this project.
- **No authentication UI, no accounts, no per-user state.** See Key decision 8
  for what replaces it.
- **No admin surface.** `/metrics` and `/ingest` are admin-scoped and stay that
  way; a browser UI for them is a second security boundary for no gain.
- **No client-side copy of the condition taxonomy.** The seam exists precisely
  so this does not happen (Key decision 1).
- **No eval-run triggering.** SPEC-007 produces a report; this renders it.
- **No design system, no component library, no CSS framework.** The frontend is
  the second item cut under time pressure (CLAUDE.md scope ladder); it must stay
  small enough that cutting it costs nothing but the cut.

## Interface

**Stack:** React + Vite + TypeScript, per CLAUDE.md's locked stack. Built to
static assets; served by the API container as static files, or by whatever
SPEC-010 chooses.

**Routes** — one page, three panels, no router:

| Region | Contents |
|---|---|
| Ask | Question input, `k` (default 8, hidden behind a disclosure), submit |
| Answer | Verdict badge, answer text with inline citation markers, citation list, usage footer |
| State | Whatever the service is currently doing instead of answering |

**Types generated from the API, not transcribed.** `openapi-typescript` against
`GET /openapi.json` at build time produces `ErrorDetail`, `QueryResponse`,
`CitationOut`, `Presentation`, `Reset`, and the `verdict` union. SPEC-006 Key
decision 15 made every response a declared model so the document is worth
generating from; hand-written interfaces would be a second copy of the contract
that drifts exactly like the two-list problem `conditions.py` exists to prevent.

**The condition renderer** is the only part of this spec with real logic:

```ts
type ConditionView = {
  tone: "explanatory" | "transient" | "degraded" | "request";
  countdown: Date | null;      // only ever non-null when reset === "window"
  retryable: boolean;
  requestId: string;
};

function view(error: ErrorDetail, retryAfter: number | null): ConditionView;
```

`tone` comes from `error.presentation` with an **unknown value falling back to
`degraded`**; `countdown` is non-null only for `reset === "window"` **and** a
present `Retry-After`; `retryable` is true for `window` and `shortly` and false
for `operator` and `none`. Unknown `reset` falls back to `shortly`. Both
fallbacks are the published contract (SPEC-006 Key decision 16, 2026-07-27).

**Streaming** is `POST /query` with `stream: true`, consumed as `text/event-stream`
per SPEC-006's frame contract: `verdict` first, `text`/`citation` interleaved,
`complete` last, `: keepalive` comments ignored, and **a stream that ends without
a `complete` frame is a failure**. A terminal `{"type":"error"}` frame carries
only `code` and `message` today, so it **cannot** be rendered through `view()`
without a client-side code map — the gap and the proposed fix are in Key
decision 4, and this line is deliberately not written as though it were closed.

## Key decisions

1. **The client renders from `presentation` and `reset`, never from `code`.**
   The registry ships both fields in every error envelope for exactly this
   reason. A frontend that switches on `code` is the second list SPEC-006 Key
   decision 16 spent a review round eliminating, and it fails in the worst
   place: a condition added server-side renders as a generic error, on the error
   path, where nobody is looking. `code` and `request_id` are shown to the
   visitor as quotable text and logged; they choose nothing.

2. **Refusal is styled as an outcome, not as an error — and this is a visual
   decision with a correctness argument behind it.** `insufficient_evidence`
   arrives as HTTP 200 with a verdict, and it means the system worked: it
   declined to fabricate. It gets the same panel as an answer, a distinct badge,
   and the retrieved excerpts that were considered and found insufficient —
   which is more informative than the answer would have been. `provider_refused`
   and `truncated` likewise. Red is reserved for conditions where the service
   failed to do its job; a refusal is the job.

3. **Citations are load-bearing UI, and `dropped_markers` is shown rather than
   swallowed.** Inline `[n]` markers resolve to the citation list; each entry
   shows `section_path` and links to `source_uri`. When the model emits a marker
   outside the valid range, SPEC-005 records it in `dropped_markers` and the API
   passes it through — the UI says so, quietly and factually. Hiding it would
   make the one observable symptom of a citation bug invisible to the only
   person positioned to notice.

4. **A mid-stream failure renders as a condition, not as a broken answer.**
   Headers went out with a 200 and the status can no longer change (SPEC-006 Key
   decision 3), so the failure arrives in-band. Partial text stays on screen,
   marked as incomplete, with the condition rendered beneath it. Clearing the
   partial answer would be a small lie in the other direction; presenting it as
   complete is the lie this avoids.

   **Blocked — the frame cannot currently be rendered this way, and this is the
   one place KD-16's cross-spec note promises something the registry does not
   deliver.** `error_frame()` emits `{"type":"error","code":…,"message":…}` and
   nothing else: no `presentation`, no `reset`, and no `Retry-After`, because
   the headers left long ago. So on the single path where the HTTP status is
   already unavailable, the two fields that exist *so a client need not keep its
   own copy of the taxonomy* are also unavailable — and the only way to render
   the frame specifically is a client-side `code` → rendering map, which is
   exactly the second list Key decision 1 forbids. The gap is narrow (one
   frame, one code path) and it is exactly where the taxonomy seam was supposed
   to pay off.

   **Proposed amendment to SPEC-006 (not applied — CLAUDE.md rule 4: an
   amendment proposed unprompted stops at proposed).** Add `presentation` and
   `reset` to the terminal error frame, from `spec_for(code)`, the same source
   `envelope()` already uses — about three lines in `sse.py`, no new concept,
   and it makes the frame self-describing exactly like the HTTP body. **Without
   it**, this spec's only honest option is to render every mid-stream failure as
   generic `degraded`/`shortly`, which is the published unknown-member fallback
   and is *correct* but throws away information the server had. AC-8 is written
   against the amendment; if it is declined, AC-8 changes to assert the generic
   rendering and this decision says so instead.

5. **`budget_exhausted` renders the explanatory panel — this is bound by SPEC-006
   Key decision 16, not chosen here.** Pre-recorded question/answer pairs
   **labeled as recorded, with their capture date**; the eval report; the
   architecture; the reset time from `Retry-After` stated as a budget guard
   rather than an outage. The label is the whole difference between this and the
   canned-answer option that spec rejected: an artifact presented as a recording
   is honest, and the same artifact presented as an answer is not. **The
   recorded pairs are a checked-in fixture with a capture date and the
   `prompt_version` they were produced under**, so a stale recording is visible
   rather than merely old.

6. **`budget_pressure` is a *transient* state and must not borrow the
   explanatory panel** *(added 2026-08-01, with SPEC-006 Key decision 16
   amendment 5)*. It means the remaining budget is committed to answers being
   generated right now; it clears in seconds, when they settle. Three
   consequences the draft has to get right:
   - **No countdown.** Its `reset` is `shortly`, and the one instant the system
     knows — the UTC reset — is the wrong one to show. Rendering the midnight
     clock here would be the same untruthfulness SPEC-006's fourth round removed,
     arriving through a different door.
   - **No explanatory panel.** The recorded pairs, the eval report, and the
     architecture are the right answer to "the demo is out of budget until
     tomorrow" and the wrong answer to "wait three seconds": a full-page state
     change for a condition that resolves before the visitor finishes reading it
     is its own kind of misinformation.
   - **The retry is offered, not automatic.** `Retry-After` is 5 seconds and the
     condition genuinely clears, so a retry button is honest. An automatic retry
     is not offered: it would turn a spend guard into a spin loop against the
     budget it is protecting, from the client, where nothing bounds it.

7. **`reset: operator` renders no clock and no retry button.** `empty_corpus`,
   `embedder_mismatch`, and `misconfigured` clear when a human changes
   something. A retry button on these is a promise the system cannot keep, and a
   countdown is the blank the `Reset` enum exists to avoid.

8. **The API key cannot live in the browser, and this spec cannot solve that
   alone. Flagged — this is the decision most likely to be wrong, and it is a
   dependency, not a detail.** SPEC-006 Key decision 14 accepts no rate limiting
   partly because "keys are not public"; shipping a public demo page with the
   read key in its JavaScript makes that premise false, and Key decision 8's
   argument for hiding the cost meter is load-bearing under exactly that
   condition. Three candidates, none of them free:
   - **Same-origin proxy.** The API serves the static assets and accepts
     same-origin `/query` with the key injected server-side. The key never
     reaches the browser; what reaches the browser is the ability to spend,
     which is the same exposure wearing a different hat — but it is at least
     one that the spend ceiling already bounds.
   - **`RAG_QA_ALLOW_ANONYMOUS` for the demo deployment**, with the spend
     ceiling as the only bound. Honest about what it is, and it makes the
     ceiling the sole guard, which is a lot of weight on one mechanism.
   - **No public URL; the frontend is run locally from the README.** Costs the
     demo its reach and keeps every premise true.

   **Recommendation: the same-origin proxy, plus a per-IP burst limit** — the
   "cheap complement" SPEC-006 Key decision 16 already names and Key decision 14
   already sizes ("a per-key token bucket on `/query` is the smallest sufficient
   change"). **This belongs in SPEC-006 or SPEC-010, not here**, and this spec
   should not be approved before that is decided somewhere — a frontend built
   against one of these three answers is not trivially portable to another.

9. **Streaming is behind a flag and is cut first.** The scope ladder cuts
   frontend streaming before it cuts the frontend. So the non-streaming path is
   the baseline and must be complete on its own, with SSE as an enhancement that
   can be deleted without touching the answer panel. This also keeps the
   time-to-first-frame problem (SPEC-006's note: with adaptive thinking the
   stream can be silent for seconds) out of the critical path — the non-stream
   path shows a spinner and is done.

10. **No client-side spend display of any kind.** The `usage` block carries
    `cost_usd` per answer and it is tempting to show it. SPEC-006 Key decision 8
    keeps the cost meter behind the admin key because an unauthenticated spend
    number is a live progress bar for anyone trying to drain the budget, and a
    per-answer figure accumulated in a browser tab is that meter, reassembled
    client-side. Token counts and latency are shown; dollars are not.

## Acceptance criteria

- **AC-1 (the taxonomy is not copied)** — The source contains no literal
  comparison against any condition `code` for rendering purposes, asserted by a
  lint rule over `src/`; the only inputs to `view()` are `presentation`,
  `reset`, and `Retry-After`. An `ErrorDetail` with `presentation: "invented"`
  renders with `tone: "degraded"`, and one with `reset: "invented"` renders
  `retryable: true` and `countdown: null` — the published fallbacks, asserted
  directly rather than inferred from the type.
- **AC-2 (every registered condition has a rendering, in both directions)** —
  A test enumerates every condition code the API can produce and asserts each
  yields a `ConditionView` with a non-empty heading; a rendering no condition can
  produce fails the same test. This is AC-17's reachability argument on the
  client side, and it is what keeps the two halves from drifting.

  **The codes come from a captured fixture, not from the OpenAPI document, and
  the distinction is worth stating because the obvious wording is wrong.**
  `openapi.json` publishes the *enum members* of `presentation` and `reset` — it
  does **not** publish the set of `code` values, which live only in
  `rag_qa/api/conditions.py`. An earlier draft of this criterion said "enumerates
  `CONDITIONS` from the OpenAPI document", which is not satisfiable. It is also
  not needed: the client renders from `presentation` and `reset` and never
  branches on `code`, so the code list is a **test** input for coverage, not a
  runtime input. The capture script writes it alongside the response fixtures,
  and a code added server-side without a rendering fails this test on the next
  capture. **KD-16's "read the rendering off the wire" is satisfied** — every
  error carries its own rendering — but "the whole taxonomy is on the wire" was
  never true and this spec does not depend on it.
- **AC-3 (a refusal is not an error)** — A 200 response with
  `verdict: "insufficient_evidence"` renders in the answer panel with the
  retrieved excerpts present, and produces **no** element carrying the error
  tone. Asserted for `provider_refused` and `truncated` too, as a parametrized
  table, so a future change cannot reclassify one quietly.
- **AC-4 (citations resolve, and dropped markers surface)** — Every inline `[n]`
  in the rendered answer resolves to a citation entry showing that entry's
  `section_path`; a response with a non-empty `dropped_markers` renders a visible
  notice naming them.
- **AC-5 (`budget_exhausted` is the explanatory state — all four elements, and the label is the load-bearing one)** — The panel contains **all four** things KD-16's cross-spec note binds: the pre-recorded Q&A pairs, the eval report, the architecture, and the reset time from `Retry-After`. Asserted element by element, since a panel missing one of them still looks like a panel.
  - **Each recorded pair carries a visible "recorded" label and its capture date**, asserted on rendered text rather than on a prop. This is the criterion that separates this spec from the canned-answer option KD-16 rejected: an artifact presented as a recording is honest, and the *same artifact* presented as an answer is the thing that was rejected. The two differ by the label and by nothing else, which is precisely why the label cannot be a styling detail.
  - **No recorded pair is rendered through the component used for a live answer**, asserted structurally. Sharing the component is how the label becomes optional later — one refactor away from the rejected design, with no test failing.
  - **Dependency, unmet:** the eval report is SPEC-007's output and **SPEC-007 does not exist**, so neither its format nor its artifact does. The recorded pairs likewise need a corpus and a real model run to capture. Both are named in KD-16's binding note as required contents of this panel; this spec cannot deliver either on its own, and neither can be stubbed without producing exactly the unlabeled-recording failure above. **This spec must not be approved as if that content were available.**
- **AC-6 (`budget_pressure` does not borrow the midnight clock — Key decision 6)**
  — Given the `budget_pressure` envelope (`presentation: transient`,
  `reset: shortly`) **and a `Retry-After` header**, the rendered output contains
  **no countdown, no reset timestamp, and none of the explanatory panel's
  contents**, and it does contain a retry control. The `Retry-After` is supplied
  in the test precisely because its presence is what would tempt an
  implementation to render a clock: a test that omits the header would pass
  against a component that renders one whenever it is present, which is the
  defect. Asserted beside `budget_exhausted` with the same fixture shape, so the
  two renderings are compared rather than each checked alone.
- **AC-7 (`operator` conditions offer nothing to wait for)** — `empty_corpus`,
  `embedder_mismatch`, and `misconfigured` render with no countdown and no retry
  control, and name the operator action.
- **AC-8 (a mid-stream failure keeps the partial answer and marks it)** — A
  stream that emits `verdict`, two `text` frames, and then a terminal `error`
  frame leaves both text fragments on screen, **marked incomplete**, with a
  condition rendered beneath them; a stream that simply ends with no `complete`
  frame renders the same way. **Conditional on the SPEC-006 amendment proposed
  in Key decision 4:** if the frame gains `presentation` and `reset`, the
  condition is rendered through `view()` like any other and this criterion also
  asserts the tone matches the code's registry entry. If the amendment is
  declined, this criterion instead asserts the failure renders as
  `degraded`/`shortly` — the published unknown-member fallback — and that **no
  code-to-rendering map exists in the source** (AC-1's lint rule already forbids
  it, and this is where it would be tempting to add an exception).
- **AC-9 (no dollar figure reaches the DOM)** — For a successful answer, a
  budget trip, and a pressure refusal, the rendered output contains no `$` and no
  decimal amount. This is Key decision 10 and AC-18's rule at the last place it
  can be broken.
- **AC-10 (the non-streaming path is complete without SSE)** — With the
  streaming flag off, the build contains no SSE code path (asserted on the
  bundle) and every criterion above except AC-8 still passes.
- **AC-11 (types come from the API)** — The generated types are produced by
  `openapi-typescript` in the build, and a CI step regenerates them and fails on
  a diff — so an API change that breaks the client breaks the build rather than
  the page.

## Test plan

`npm run test` — Vitest with Testing Library, jsdom. `npm run lint`,
`npm run build`. Added to `.github/workflows/ci.yml` as a **second job**, not as
a fourth gate in the existing one: the Python gates run against a pgvector
service container that a node build has no use for, and serializing them would
make every backend change wait on a frontend install.

**Every acceptance criterion here is verified by breaking the behaviour it
covers** (CLAUDE.md rule 3), and this frontend has two shapes especially prone
to passing while proving nothing:

- **A condition test that asserts on the view object rather than on the rendered
  output.** `view()` returning `countdown: null` is an intermediate; what matters
  is that no clock is on the screen. Rule 3's closing sentence names this exact
  shape, and AC-6 is the criterion where getting it wrong would be invisible —
  so AC-6 asserts on rendered text, not on the return value.
- **A test whose fixture makes its subject unreachable** — the sixth instance in
  rule 3's list. AC-6 supplies a `Retry-After` header for this reason: without
  it, a component that renders a countdown whenever the header is present would
  pass a test that never gives it one.

**Fixtures are recorded from the real API, not hand-written.** A checked-in
capture script runs the app against the stub retriever and the fake LLM client
(the SPEC-006 harness) and writes the response bodies used by these tests. A
hand-written `QueryResponse` fixture is a third copy of the contract, and it
drifts silently in the direction that makes the tests pass.

**No network in any test.** No API process, no provider, no database — the
fixtures are files, and AC-2's enumeration reads a checked-in copy of
`openapi.json` regenerated by the same script.
