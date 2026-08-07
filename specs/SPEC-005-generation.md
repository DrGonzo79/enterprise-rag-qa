# SPEC-005 — Answer Generation

**Status:** Approved — 2026-07-26 (with review amendments: verdict/thinking coupling recorded as Key decision 7; default model changed to `claude-sonnet-5` with the Opus-vs-Sonnet comparison handed to SPEC-007 as a measurement; pricing verified against the published documentation rather than fixed in prose; binding SPEC-007 note separating citation precision from groundedness; `answer_text` retention recorded as a demo-only choice; AC-7a added for verdict-token buffering in streaming). **Second review, 2026-07-26:** new Key decision 16 — cost recomputation resolves the rate from the request's own `created_at`, never the current date, with AC-9a asserting a recomputation across the 2026-09-01 boundary; Key decision 10 amended — no OpenAI rate row ships, so the README quickstart states the constructor failure and the error message names the *provider's own* pricing page. **Amended 2026-07-26 by SPEC-006:** AC-11's `POST /ask` is superseded by `POST /query`; `rag_qa/generation/api.py` is removed and the HTTP layer moves to SPEC-006.
**Date:** 2026-07-26
**Depends on:** SPEC-004

## Purpose

Turn a question plus retrieved chunks into a cited, grounded answer: a system prompt that permits answers only from supplied context, inline citation markers resolved back to `chunks.section_path` so answers cite "Article 6(2)" rather than a chunk id, an explicit refusal path when the evidence does not support an answer, streaming with citations parseable from the stream, and one `query_log` row per request carrying latency, tokens, and cost.

Generation sits behind a provider-agnostic `LLMClient` protocol with Anthropic and OpenAI implementations, swappable with zero call-site changes — the charter's model-agnostic adapter requirement.

Two invariants carry forward from earlier specs:

1. **Generator identity is recorded per request**, sourced from the client rather than a constant — the same rule that fixed the embedder defect in SPEC-004 (KD-4). A model swap must be visible in the data, never inferred from a deploy date.
2. **Refusal is a first-class, machine-readable outcome**, not a string match on the answer text. Refusal is a charter-level scored capability; a capability measured by grepping for "I don't have enough information" is not measured.

## Non-goals

- Retrieval — `answer(question, chunks)` takes chunks as given; SPEC-004 owns getting them
- Reranking, query rewriting, multi-turn conversation, follow-up questions
- Groundedness / faithfulness scoring — that needs an LLM judge and is SPEC-007's
- Prompt-injection defense against corpus content (see Key decision 13 — deliberate, bounded)
- Prompt caching (see Key decision 14 — measured as not worth it yet, with the trigger for revisiting)
- Tool use / function calling in the generation path — the model answers from context, it does not call back for more
- Fine-tuning, few-shot example selection, or a prompt-optimization loop
- Cost *attribution* across tenants — one deployment, one bill

## Interface

### Modules

```
src/rag_qa/generation/
    __init__.py       # re-exports: Generator, Answer, Citation, Verdict, LLMClient
    types.py          # Answer, Citation, Verdict, stream events, errors
    prompt.py         # SYSTEM_PROMPT, PROMPT_VERSION, render_context()
    citations.py      # incremental marker parser (streaming-safe), validation
    clients/
        base.py       # LLMClient protocol, LLMResult, LLMStreamEvent
        anthropic.py  # AnthropicClient
        openai.py     # OpenAIClient
    pricing.py        # per-identity token pricing; compute_cost (live) +
                      #   recompute_cost (from query_log.created_at — KD-16)
    service.py        # Generator.answer() / .stream_answer(), query_log write
    # api.py          # REMOVED by SPEC-006 — POST /ask became POST /query and
    #                 #   the HTTP layer moved to rag_qa/api/ (AC-11, amended)
alembic/versions/0004_*.py   # query_log: answer_text, verdict, prompt_version
```

### Types

```python
class Verdict(StrEnum):
    ANSWERED = "answered"  # evidence supported an answer
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # the model declined — a SUCCESS
    TRUNCATED = "truncated"  # hit max_tokens mid-answer
    PROVIDER_REFUSED = "provider_refused"  # provider safety classifier declined (KD-5)
    ERROR = "error"  # provider/transport failure


@dataclass(frozen=True)
class Citation:
    marker: int  # the n in [n], 1-based, as the model wrote it
    chunk_id: UUID
    section_path: str  # "EU AI Act › CHAPTER III › SECTION 1 › Article 6 — …"
    document_title: str
    source_uri: str


@dataclass(frozen=True)
class Answer:
    text: str  # verdict line stripped; [n] markers retained in place
    verdict: Verdict
    citations: tuple[Citation, ...]  # deduplicated, in first-appearance order
    generator_identity: str  # "anthropic:claude-sonnet-5" — from the client
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    dropped_markers: tuple[int, ...]  # out-of-range markers stripped from text (KD-9)
```

### `Generator`

```python
class Generator:
    def __init__(
        self,
        client: LLMClient,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        max_tokens: int = 4096,
    ) -> None: ...

    async def answer(self, question: str, chunks: Sequence[RetrievedChunk]) -> Answer: ...

    async def stream_answer(
        self, question: str, chunks: Sequence[RetrievedChunk]
    ) -> AsyncIterator[AnswerEvent]: ...
```

`session_factory` is optional so `answer()` is usable in tests and eval runs without a database; when present, every completed call writes one `query_log` row.

### `LLMClient` protocol — the provider seam

```python
@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    stop: StopKind  # NORMAL | MAX_TOKENS | REFUSAL


class LLMClient(Protocol):
    identity: str  # "provider:model" — same pattern as EmbeddingClient
    provider: str  # "anthropic" | "openai" — for query_log (KD-8)
    model: str

    async def complete(self, system: str, user: str, max_tokens: int) -> LLMResult: ...
    def stream(
        self, system: str, user: str, max_tokens: int
    ) -> AbstractAsyncContextManager[AsyncIterator[LLMStreamEvent]]: ...
```

`LLMStreamEvent` is `TextChunk(text)` or `Usage(prompt_tokens, completion_tokens, stop)`. **Everything provider-shaped stays inside the adapter**: Anthropic's `input_tokens`/`output_tokens` and OpenAI's `prompt_tokens`/`completion_tokens` both normalize to the latter pair; Anthropic's `content_block_delta`/`message_delta` events and OpenAI's chunk deltas both normalize to `TextChunk`/`Usage`. Callers never import a provider SDK type.

**Defaults:** `AnthropicClient(model="claude-sonnet-5")` → identity `anthropic:claude-sonnet-5` (Key decision 15; Opus 5 is one constructor argument away). `OpenAIClient` is the second provider and the first item on the scope-cut ladder above the never-cut line. Anthropic-side request shape (verified against the current API, 2026-07-26):

- **No `temperature`** — see Key decision 4. Sending it returns 400 on current Claude models.
- **Thinking left at its default (adaptive, on)** — see Key decision 6. `display` stays `"omitted"`; the adapter drops `thinking` deltas and forwards only `text_delta`.
- `max_tokens=4096` default — generous for a cited compliance answer, and it bounds thinking + text together.
- Streaming via `client.messages.stream(...)`; usage read from the final message.

### System prompt (`prompt.py`)

`PROMPT_VERSION = "v1"`, recorded on every `query_log` row and in `eval_runs.config`. Behavioral requirements, in the prompt's own order:

1. **First line is a machine-readable verdict token** — exactly `ANSWERED` or `INSUFFICIENT_EVIDENCE`, alone on the line, before any prose. Parsed and stripped from `Answer.text` (Key decision 3).
2. **Answer only from the numbered excerpts.** No outside knowledge, no inference beyond what the excerpts state. If the excerpts are silent, partial, or only tangentially related, emit `INSUFFICIENT_EVIDENCE` and briefly say what is missing.
3. **Every factual sentence carries at least one `[n]` marker** naming the excerpt it came from. Markers go inline at the end of the clause they support.
4. **Cite by excerpt number only.** The section path is shown in each excerpt header and is resolved by the application — the model must not invent or reformat citation strings.
5. **Do not use an excerpt number that was not provided.**
6. Declining is a correct outcome, not a failure. Do not stretch weak evidence into an answer.

### Context rendering (`render_context()`)

```
[1] EU AI Act › CHAPTER III › SECTION 1 › Article 6 — Classification rules…
<chunk text>

[2] NIST AI RMF 1.0 › AI RMF Core › Govern
<chunk text>
```

The header is `section_path`; the body is `chunk.text` **as stored**, breadcrumb prefix and all (Key decision 12). Markers are 1-based and assigned in the order chunks arrive — i.e. fused retrieval rank order — so `[1]` is the best-ranked chunk.

### Citation parsing (`citations.py`)

A marker may be split across stream chunks (`[` … `12]`), so the parser is incremental with bounded lookahead: on `[`, buffer until `]`, a non-digit, or 8 buffered characters (`[` + up to 6 digits + `]`), whichever comes first; a non-marker buffer is flushed verbatim as text. Resolution maps `n` → `chunks[n-1]`; out-of-range `n` is stripped from the text and recorded in `dropped_markers`.

### Streaming (`stream_answer`)

Events, in guaranteed order:

```python
VerdictEvent(verdict)  # FIRST — before any text
TextDelta(text)  # zero or more
CitationEvent(citation)  # interleaved, at each resolved marker
AnswerComplete(answer)  # LAST — carries usage, cost, latency
```

Emitting the verdict first is a direct consequence of the verdict-token design: a client can render "no supporting evidence found" immediately instead of streaming a paragraph that turns out to be a refusal. `AnswerComplete` is the only event carrying usage, because the provider only reports it at the end of the stream.

### HTTP surface — moved to SPEC-006

```
POST /query   {"question": "...", "k": 8, "filters": {...}, "stream": false}
→ {"answer": "...", "verdict": "answered", "citations": [...], "usage": {...}}
```

Originally `POST /ask`, owned here. **SPEC-006 owns the HTTP layer and renamed it to `POST /query` with no alias** (SPEC-006 KD-2); `rag_qa/generation/api.py` is removed. The orchestration is unchanged — `retrieve()` → `answer()` → `query_log` write, `StreamingResponse` over the event sequence above for `stream: true`, and retrieval errors (`EmbedderMismatchError`, `EmptyCorpusError`) surfacing as 503 because a corpus/embedder mismatch is an operational fault rather than a bad request.

### Migration `0004` — `query_log`

Three columns, each justified by a named failure mode rather than by anticipated need:

| Column | Type | Why |
|---|---|---|
| `verdict` | text not null | Refusal is a charter-scored capability. Without it, measuring refusal rate means string-matching answer text — the brittleness this spec exists to avoid. |
| `answer_text` | text not null | Without it the log records token counts and costs *about text nobody kept*. Post-hoc groundedness review, debugging a bad answer, and SPEC-007's judge all need the actual output. Empty string for `ERROR`. |
| `prompt_version` | text not null | The prompt will be tuned repeatedly. Without this, logged answers cannot be attributed to the prompt that produced them — the same silent-drift failure that `embedding_model` prevents for vectors. |

Deliberately **not** added: `citation_count` (derivable by re-parsing `answer_text`), cache-token columns (caching is a non-goal, KD-14), `retrieval_ms` (retrieval owns its own instrumentation). The existing `provider` / `model` columns are used as-is (Key decision 8).

**`answer_text` retention is a demonstration-deployment choice and would require a retention policy in production** *(added after review)*. It is defensible here because the corpus is public regulation and public filings, and there is no authentication and no user account a question could be attached to (SPEC-000 non-goals) — so the log is a record of anonymous questions about public documents, and its debugging and post-hoc-groundedness value is high. **Those properties invert the moment there are real users:** `query_log.question` alongside `answer_text` becomes a durable record of what identifiable people asked and what they were told, which is exactly the shape that attracts retention limits, deletion requests, and access control. Anything built on this schema for real traffic needs a defined retention window, a deletion path, and a decision about whether question text is stored at all. **None of that is in scope here, and this note exists so its absence is a recorded choice rather than an oversight.**

### New dependencies

`anthropic`. `openai` is already present (embeddings, SPEC-003).

## Key decisions

1. **Inline `[n]` markers over the provider's native citations API — flagged, arguing against the obvious choice.** The obvious move for an Anthropic-first project is Anthropic's built-in citations feature: it returns structured `cited_text` with document indices and character offsets, and it is more precise than anything parsed from prose. **Rejected because it would make the adapter leak.** `Answer.citations` would then carry a shape only one provider can produce, and `OpenAIClient` would need an emulation that behaves differently — breaking "swappable with zero call-site changes," which is a charter-level commitment, not a preference. Inline markers cost ~2 tokens each, stream naturally (a `[3]` is self-delimiting), validate trivially (in range or not), and behave identically across providers. **Cost of the choice, stated plainly:** markers are model-authored, so a model can attach the wrong number to a true sentence, and nothing here detects that — only SPEC-007's judge can. Native citations would have made *that specific* error less likely. If provider portability is ever dropped, this decision should be revisited first.
2. **Refusal is decided from the retrieved content by the model, not from a score threshold — and I argue *for* it, with one qualification.** The constraint from SPEC-004 KD-9 is real: RRF scores are rank-derived, bounded near 2/61, and encode agreement between two lists rather than calibrated relevance, so they are not comparable across queries and a cutoff on them would be arbitrary. But the stronger argument is that the question is *semantic*: "do these passages support an answer" cannot be read off any similarity number, because a chunk can be the nearest neighbour and still not contain the answer. Only something that reads the text can judge that. **The qualification, and it matters: prompt-only refusal has a known failure direction — under-refusal.** Models are agreeable and will synthesize a confident answer from tangentially related regulatory prose, which for a compliance corpus is the expensive error. This spec does not solve that; it makes it *visible and measurable* via the structured verdict (KD-3) and hands SPEC-007 a clean signal to score. **One non-model refusal is kept:** zero chunks retrieved refuses without calling the LLM at all — that is an absence, not a threshold, and it saves a pointless request.
3. **A structured verdict token, not string-matching the refusal — flagged, arguing against the obvious choice.** The obvious implementation is "instruct the model to say it can't answer, then check whether the reply looks like a refusal." That makes a charter-scored capability depend on phrasing, and it silently rots the moment the prompt is retuned or the model's wording drifts. The verdict is instead the first line of the response, parsed and stripped: refusal becomes a parsed field, not an inference. Three payoffs: `query_log.verdict` is directly aggregatable; SPEC-007 scores refusal without a text heuristic; and in streaming the verdict arrives before any prose, so a client never renders half an answer that turns out to be a decline. **Rejected alternative: assistant prefill** to force the token — prefills return 400 on current Claude models. **Rejected alternative: structured outputs (`output_config.format`)** — it would guarantee the shape, but JSON does not stream usefully as prose, and streaming is an explicit requirement here.
4. **Temperature 0 is specified in the brief and is NOT AVAILABLE on current Claude models — flagged, contradicting the request.** `temperature`, `top_p`, and `top_k` were removed from the Claude API: on Claude Opus 5 sending `temperature` at all returns **HTTP 400**, and on Claude Sonnet 5 any non-default value does. There is no equivalent knob; the replacement lever is `output_config.effort` plus prompting. So the protocol **does not expose temperature at all** — each adapter does what its provider supports (`OpenAIClient` sets `temperature=0`; `AnthropicClient` sends nothing). Two consequences worth stating: the two providers are then not configured identically, which is a real if unavoidable asymmetry under a provider-agnostic seam; and **temperature 0 never guaranteed determinism anyway** — provider-side batching makes outputs vary run to run — so an acceptance criterion asserting byte-identical answers across runs would have been flaky on either provider (see Key decision 11). Options if determinism matters more than the current model: pin an older Claude that still accepts sampling parameters (rejected — a deprecated path for a demo), or accept prompting-based stability (chosen).
5. **`stop_reason == "refusal"` is NOT our refusal verdict, and conflating them would corrupt the metric — flagged.** Claude Opus 5 runs safety classifiers that can decline a request outright, returning HTTP 200 with `stop_reason: "refusal"` and an empty or partial body. That is a *provider* decline, semantically unrelated to "the retrieved evidence doesn't support an answer." **This corpus makes the collision likely rather than theoretical:** NIS2, the Cyber Resilience Act, and AI Act Article 15 are all cybersecurity text, and the classifiers target cyber content — a legitimate question about incident-reporting obligations can trip them. If both landed in one bucket, the refusal rate the charter scores would silently blend "correctly declined for lack of evidence" with "provider wouldn't answer," and a classifier tuning change would look like a retrieval regression. They get distinct verdicts (`INSUFFICIENT_EVIDENCE` vs `PROVIDER_REFUSED`), and **`response.stop_reason` is checked before `response.content` is read** — code that indexes `content[0]` unconditionally raises on a refusal.
6. **Adaptive thinking stays enabled, and disabling it is actively unsafe for this design — flagged, arguing against the obvious choice.** *(Read with Key decision 7, which gives the second, behavioral reason.)* The obvious call for a latency-sensitive answer path is `thinking: {type: "disabled"}` — it is cheaper and gets to the first token sooner. **Rejected on a specific, documented failure mode: with thinking disabled, Claude Opus 5 can leak `<thinking>` tags into the visible response.** This pipeline parses the visible response for `[n]` markers and a verdict token on the first line; stray XML in that stream corrupts both. The cheaper knob is `output_config.effort`, which reduces spend without changing output shape. Thinking is on by default on Opus 5, `display` stays `"omitted"`, and the adapter forwards only `text_delta` events — so thinking costs tokens and time but never reaches the parser. **Consequence to accept:** with streaming, the user waits through the thinking phase before the first visible token; `AnswerComplete` timing therefore reflects thinking, and AC-9's latency budget is set against measurement rather than a guess.
7. **The verdict token and extended thinking are coupled: the first-line verdict is only safe *because* thinking is on — flagged, and the two reasons are recorded together deliberately.** Key decision 3 requires the model to emit `ANSWERED` or `INSUFFICIENT_EVIDENCE` as the first line, before any prose. That is a real cost: **it commits the model to a position before it has written a single word of reasoning about the evidence.** With thinking enabled, the commitment is made *after* the model has actually reasoned over the excerpts — the verdict is the conclusion of hidden work, and the prose that follows elaborates a decision already taken on the evidence. With thinking disabled, the first token generated in the entire response is the verdict, so the model commits essentially on priors, and then has to write an answer consistent with a claim it made before looking. **That is confabulation pressure, pointing in the expensive direction:** a model that has already emitted `ANSWERED` is under generative pressure to produce an answer, which compounds the under-refusal failure named in Key decision 2 rather than surfacing it.

   **This is the second independent reason not to disable thinking, and it is recorded here with the first so a future cost-cutting change sees both.** Key decision 6's reason is mechanical — `<thinking>` tag leakage corrupts the marker and verdict parser. This one is behavioral and would not be caught by any parser test: output that parses perfectly and is confidently wrong. A change that disables thinking for cost or latency must either keep a verdict-first contract and accept both risks, or move the verdict to the *end* of the response — which would in turn forfeit the streaming benefit that motivated putting it first (a client could no longer render "no supporting evidence" before the prose). **The three choices are entangled; none of them can be revisited alone.**

   ---

   **Amendment 1 — the predicted pressure is real, and it produced a failure mode this decision did not predict** *(2026-08-07, from `evals/prereg-refusal-nearmiss.md`; raised as Proposed, **approved by owner review the same day** — "fix the field, because it outruns this experiment")*.

   **Measured, 20 unanswerable questions, thinking ON as this decision requires: 13 of 20 answers whose body declines carried `verdict: answered`.** Zero came back `ERROR`, and `AnswerParser._feed_verdict` defaults to `ERROR` on any unrecognised first line — so every one of those 13 first lines was literally `ANSWERED`, and the model then declined underneath it. Two stop and correct themselves mid-answer: *"Correction: … the correct verdict is: INSUFFICIENT_EVIDENCE"* and *"Wait, let me correct this properly. INSUFFICIENT_EVIDENCE"*.

   **What the decision got right and what it missed.** It predicted the pressure and named its direction — *"a model that has already emitted `ANSWERED` is under generative pressure to produce an answer, which compounds the under-refusal failure"*. **The pressure is real. The consequence it produced is not under-refusal.** Content refusal was **20 of 20**. What broke is the **agreement between the verdict and the body**, and that is worse in one specific way: the prose is correct, so nothing that reads the answer looks wrong, while everything that reads the field is wrong silently — SPEC-006's response contract, and SPEC-009, which is planned to render a refusal differently from an answer.

   **This is CLAUDE.md rule 9 landing on a spec's prediction rather than on a test's falsifier.** The mechanism was named correctly and the observable bound to it was the wrong one: under-refusal would show in the verdict *rate*, and this shows only in verdict-versus-body *agreement*, which nothing measures. AC-3's determinism assertion pins that the verdict is *stable*, not that it is *right*.

   **Why this is a decision and not a bug fix.** Verdict-first exists so a streaming client knows early what it is receiving; verdict-first is *why* the model commits before it reasons. The affordance and the defect are the same design choice, so the question is which cost to pay. **Four options, judged on correctness first, with the effect on the refusal metric quarantined** — a fix chosen because it improves a number is a fix chosen by the number.

   | option | correctness | cost to the SSE contract |
   |---|---|---|
   | **A. Trailing verdict only** — move the token to the end | High. The verdict is the conclusion of visible reasoning. | **Fatal.** The first frame is the entire point; a client would render prose and *then* learn it was a decline — worse than no early signal, because it has already displayed a refusal as an answer in progress. |
   | **B. Second cheap call** — a small model classifies the produced answer | Medium, and **borrowed**. It replaces one component's error rate with another's, and the classifier's is unmeasured. | The first frame is still wrong and already rendered; only `complete` could carry the correction. Adds per-query latency and cost on the hot path. |
   | **C. Structured output** — force a `verdict` field via a JSON schema | **Does not address the failure.** A schema constrains the *shape*, not the *agreement*: the model can still set `verdict: "answered"` and write a declining `answer` string. | Large. Streaming partial JSON needs a JSON stream parser to recover text deltas, and `AnswerParser` — with its marker and leak guards — would be replaced wholesale. |
   | **D. Reconciliation: keep the header, add a trailing token, the trailing one is authoritative** | **High, and cheapest.** The model states the verdict twice: once before reasoning, once after. Nothing infers a verdict from prose — which is exactly what `verdict` was made a column to avoid (migration 0004). | **Small and additive.** The first frame is unchanged except for a `provisional` flag; a corrective `VerdictEvent` follows **only on disagreement**; `complete` carries both. A client that ignores the new field gets last-write-wins, which is the right default. |

   **Chosen: D.** It is the only one of the four that keeps the streaming affordance, adds no component whose error rate is unknown, and — the deciding property — **makes the failure measurable instead of invisible.** A and B and C all produce one verdict; D produces two and stores both, so the disagreement rate is a number someone can watch. The v1 failure survived because nothing could have shown it.

   > **A rejected alternative worth naming: string-matching the body for a decline.** It recovers only 7 of the 13 (the rest decline in prose without the token), and it is the mechanism migration 0004 created the `verdict` column specifically to avoid. A fix that reintroduces prose-matching to repair a field whose purpose was to eliminate prose-matching is a circle.

   **What shipped.** Prompt **v2** requires the token on the first line *and* as the final line alone, and says outright that changing your mind is correct rather than an error to hide. `AnswerParser` intercepts a verdict token **alone on a line** — never one followed by prose, because v1's answers repeatedly wrote `INSUFFICIENT_EVIDENCE — the excerpts are…`, which is an answer and must render as one — and **the last such line wins**, because two of v1's answers corrected themselves mid-response. `Answer` carries `verdict` (authoritative), `provisional_verdict` (the header) and `verdict_reconciled`. Migration **0007** adds `query_log.provisional_verdict`, nullable with no backfill: rows written under v1 have no header/body distinction to record, and copying `verdict` into it would manufacture agreement nobody measured.

   **The cost, stated rather than left to be discovered:** the lookahead holds a line while it remains a viable prefix of a verdict token — at most 21 characters, released the instant it is not. **A test asserts body text streams without waiting for a newline**, because a line-buffering regression preserves `text` exactly and is invisible to every assertion about content. That mutation survived the first pass.

8. **`query_log` keeps separate `provider` and `model` columns; the identity string is derived, not stored again — flagged, deliberately NOT copying the embedder pattern.** The brief asks for generator identity "same pattern as embedder identity." The *invariant* is copied exactly — the value comes from `client.identity`, never a constant, so a model swap is visible in the data. The *storage shape* is not, and the reason is that SPEC-004 KD-3 argued for one opaque string precisely because the only operation on embedder identity is equality comparison. Generator identity is different: `query_log` exists to be **queried analytically** — cost by provider, latency by model, refusal rate across a swap — which is exactly the provider-level querying KD-3 said would justify splitting. SPEC-002 already provides both columns. Adding a third redundant column would store the same fact twice and invite them to disagree.
9. **Out-of-range citation markers are stripped and counted, not fatal — flagged.** The obvious options are to fail the request (a hallucinated `[9]` among 8 chunks means the output is untrustworthy) or to pass it through. Both are wrong: failing discards a good answer over one bad marker, and passing through renders a citation that resolves to nothing. Markers outside `1..len(chunks)` are removed from the text and recorded in `Answer.dropped_markers`, and the count is logged. **Silent stripping alone would be the real error** — if bad markers are invisible, nobody learns the prompt is producing them.
10. **Pricing is a per-identity table, and an unknown model is a hard error at construction — flagged, arguing against the obvious choice.** The obvious fallback for an unpriced model is `cost_usd = 0` with a warning. `cost_usd` is `not null`, so that writes a *lie* into a column whose entire purpose is cost tracking, and warnings in an untailed log are how silent corruption happens (the same reasoning as SPEC-004 KD-4). Instead the client refuses to construct: swapping models forces a pricing-table update in the same change. Rates are applied at request time and stored point-in-time, per SPEC-002's existing decision — token counts alone cannot reconstruct cost after a price change. Rates are verified against the published pricing documentation (checked 2026-07-26), and each row records that date and its source URL rather than restating figures in prose: `anthropic:claude-sonnet-5` $2/$10 per MTok through 2026-08-31 then $3/$15; `anthropic:claude-opus-5` $5/$25. The Sonnet 5 expiry is **carried as data with a date, not a comment** — a comment cannot switch the rate on 2026-09-01.

    **Amended 2026-07-26 (second review) — the "swappable with zero call-site changes" claim holds in tests and fails on a fresh clone, and that gap is now closed by documentation rather than by inventing a rate.** No OpenAI rate row ships, so `OpenAIClient(model=…)` raises `UnknownModelError` at construction on a clean checkout. That is KD-10 working as designed, but a reader who has only seen the charter's model-agnostic-adapter claim meets it as a surprise — the same class of defect as the `.env` loading bug SPEC-001 fixed: correct behavior, discoverable only by tripping over it. **Two fixes, and deliberately not a third.**
    - **The README quickstart says it out loud**, in the section on swapping providers: `OpenAIClient` needs a verified rate row before first use, here is the file, here is the page to verify against, here is why an unpriced model is a hard error rather than `cost_usd = 0`.
    - **The exception names the *provider's* pricing page, not Anthropic's.** Previously every message pointed at `docs.claude.com` regardless of identity, so a user adding an OpenAI model was told to verify an OpenAI rate against Anthropic's price list. Guidance that misdirects on the one path it exists to serve is worse than none; sources are now per-provider and the message resolves by identity prefix.
    - **Not done: shipping OpenAI rate rows.** Rejected because a rate row requires naming a model, and **no spec has chosen an OpenAI model** — the charter names OpenAI as "second provider", nothing more. Adding `openai:gpt-…` to a pricing table would make an unmeasured model choice by implementation, which SPEC-000 rule 6 exists to prevent, for the provider that is *first on the scope-cut ladder* above the never-cut line. A row costs nothing to add when someone actually swaps; a wrong default model chosen today would be inherited silently. **Asserted, so this stays a decision rather than a drift:** a test pins that no `openai:` row exists *and* that the README documents the requirement — adding a row fails that test until the note is updated with it.
11. **Determinism is asserted as request-shape and semantic stability, never byte equality — flagged (minor).** The obvious AC for "temperature 0" is "same question and chunks produce the same answer." That will flake: provider-side nondeterminism means identical requests can differ, independent of sampling parameters (which, per KD-4, we no longer send). AC-3 instead asserts what is actually controllable — that no sampling parameter is sent to Anthropic, that `temperature=0` is sent to OpenAI, and that the *verdict and cited chunk set* are stable across repeated runs — leaving prose wording free to vary.
12. **Breadcrumb prefixing (SPEC-003 KD-5) helps generation more than it hurts, and the balance shifts with corpus size.** Asked for explicitly, so stated in both directions.
    - **Helps, and increasingly:** the model sees provenance inline, so "cite the excerpt's section" needs no separate metadata block. More important, **it prevents cross-document conflation** — post-expansion, the AI Act, the Machinery Regulation, the CRA, and the MDR all discuss conformity assessment in near-identical language, and without the breadcrumb the model has no in-context signal that two excerpts come from *different instruments*. Blending two regulations' requirements into one confident answer is the worst failure this system can produce, and the breadcrumb is the cheapest mitigation.
    - **Hurts, three ways:** it costs tokens (~15–25 per chunk × 8 chunks ≈ 120–200 per request, ~3% of a typical prompt); repeated identical prefixes across several chunks from one document may bias the model toward that document; and — the subtle one — **a chunk's breadcrumb can be more specific than its body**. Overlap and continuation chunks carry the breadcrumb of the section they started in, so a chunk labelled "Article 6" may contain trailing text from Article 7, and the model may cite Article 6 for a claim the body took from elsewhere. That is a *mis-citation with a correct-looking section path*, which is harder to spot than an obviously wrong one.
    - **Two design consequences, both binding:** `section_path` is **not** rendered separately in the prompt — it is already in the chunk text, and rendering both would duplicate tokens and let the two copies disagree. And SPEC-007's groundedness judge must check citations against the *chunk body*, not against the breadcrumb, or it will validate exactly the mis-citations described above.
13. **Prompt-injection defense is out of scope, and this is safe only because of what the corpus is — flagged so the assumption is explicit.** Retrieved chunks are model-visible text from a corpus that could in principle carry instructions ("ignore previous instructions and…"). It is out of scope because every corpus document is a published regulation or a filed 10-K from a named public source, fetched over HTTPS and committed to the repo — a threat model with no untrusted writer. **This stops being true the moment the corpus accepts user-supplied or scraped documents**, which is the point at which delimiting and instruction-hierarchy defenses become required rather than optional. Recorded here so that change is recognized as a security boundary rather than a corpus expansion.
14. **No prompt caching in v1 — measured, not assumed.** The obvious optimization is caching the system prompt. Claude Opus 5's minimum cacheable prefix is **512 tokens** and the system prompt is roughly that size, so it sits right at the threshold where caching may silently not happen. The retrieved chunks — the bulk of the prompt — are different on every query and are not cacheable at all. So the ceiling is a ~10% input-token saving on a best case, against a 1.25× write premium and a cost formula that would need three rates instead of one. **Revisit when** the system prompt grows past ~1,000 tokens or a stable few-shot preamble is added; at that point `cache_read_input_tokens` and `cache_creation_input_tokens` must enter the cost calculation, and possibly `query_log`.
15. **`claude-sonnet-5` is the default; the model is a constructor argument, and Opus-vs-Sonnet is a measurement SPEC-007 owns** *(amended after review — an earlier draft defaulted to Opus 5 and deferred the choice)*. Verified against the pricing documentation on 2026-07-26: Sonnet 5 is **$2 / $10 per MTok through 2026-08-31**, then **$3 / $15**; Opus 5 is **$5 / $25**. At the introductory rate that is 2.5× cheaper on both input and output, and 1.67× cheaper afterwards. For *extractive* answering — synthesizing a cited answer from excerpts already selected by retrieval — the capability gap between the tiers is far narrower than on open-ended reasoning, because the hard part (finding the evidence) has already happened. A project that re-runs its eval suite dozens of times during tuning pays that multiple on every run.

    **This is a default, not a finding.** It is chosen on cost and on a plausible argument about the task shape, and it is explicitly *not* backed by a measurement yet — SPEC-007 owns that comparison (see the cross-spec note under AC-9). If the golden set shows Sonnet 5 materially behind on refusal correctness or citation accuracy, the fix is one constructor argument. **Do not let the default harden into an assumption**: the reason the model is a constructor argument rather than a module constant is precisely so this stays a measured question. **Pricing figures are verified against the documentation rather than fixed here** — the pricing table is code, dated, with its source recorded, and re-verified whenever a model is added.

16. **Cost recomputation resolves the rate from the request's own `created_at`, never from the current date — and the request is priced at the same timestamp that is stored** *(added 2026-07-26, second review)*. The Sonnet 5 introductory rate expires **on 2026-09-01, mid-project**, so `query_log` will legitimately hold rows at two different rates for the same `provider`/`model` pair. Any recomputation — verifying a stored total, backfilling rows logged before a rate row existed, or reporting spend across a date range — that resolves rates "as of today" reprices history at whatever the current rate happens to be, and does so *plausibly*: the numbers stay the right order of magnitude, so the error does not announce itself. `compute_cost` prices the live path (now); `recompute_cost` prices a logged row from its `created_at` and is the only correct entry point for anything historical.

    **Two supporting choices, both load-bearing:**
    - **`created_at` is set by the application from the same timestamp used to price the request**, rather than left to the column's `now()` server default. Otherwise a request beginning at 23:59:59.999 on 2026-08-31 and inserted milliseconds later stores a cost computed at the introductory rate against a `created_at` that says the request happened after the change — and every later recomputation "corrects" a row that was already right. The window is tiny and the fix is one argument; leaving it open means the stored cost and the column that reprices it can disagree, which makes reconciliation unusable exactly when it matters. The server default remains for rows inserted by anything other than this code path.
    - **A naive datetime is rejected, not assumed UTC.** `query_log.created_at` is `timestamptz`; a naive value read back and interpreted in local time shifts the date by up to a day, which on 2026-08-31 lands on the wrong side of the rate change. Raising is the only safe reading — the alternative is a silent off-by-one-rate on precisely the boundary this decision exists for.

    **Stored cost stays authoritative.** `recompute_cost` is a verification and backfill tool, never a silent rewrite of `cost_usd`: SPEC-002 stores cost point-in-time precisely because token counts alone cannot reconstruct it, and a recomputation that disagrees with a stored value is a *finding* — a wrong rate row, a clock problem, a model swapped without a pricing update — not something to paper over by overwriting the column.

## Acceptance criteria

- **AC-1 (contract)** — With a fake `LLMClient` returning a fixed response: `answer(q, chunks)` returns an `Answer` whose `text` excludes the verdict line, whose `citations` resolve to the passed chunks, and whose `generator_identity`, `prompt_version`, `prompt_tokens`, `completion_tokens`, `cost_usd`, and `latency_ms` are all populated. Citations are deduplicated and in first-appearance order.
- **AC-2 (provider swap, zero call-site change)** — The same `Generator` construction and the same `answer()` call produce equivalent `Answer` shapes with `AnthropicClient` and `OpenAIClient` fakes; no test imports a provider SDK type; `Answer` carries no provider-specific field. Asserted structurally: the union of attribute names on `Answer` is identical across both.
- **AC-3 (sampling parameters, Key decision 4)** — The Anthropic adapter's outgoing request contains **no** `temperature`, `top_p`, or `top_k` key (asserted on a captured request payload — sending one is a 400 on current models). The OpenAI adapter sends `temperature=0`. Across 3 runs against a stubbed client with varied wording, `verdict` and the set of cited `chunk_id`s are identical; prose text is not asserted equal.
- **AC-4 (context-only answering)** — Given chunks that do not contain the answer, the model's contract is exercised via a stubbed client asserting the rendered prompt: every chunk appears under a numbered `[n]` header carrying its `section_path`; the question appears; and no chunk text is truncated or reordered relative to input order.
- **AC-5 (citations)** — `[1]`…`[n]` resolve to `chunks[0]`…`chunks[n-1]` with correct `section_path`; a marker split across two stream chunks (`"…text ["` then `"2] more"`) resolves correctly; an out-of-range `[9]` with 8 chunks is stripped from `text`, recorded in `dropped_markers`, and logged; a bare `[` never emitted as a marker is flushed as literal text.
- **AC-6 (refusal path)** — (a) A response beginning `INSUFFICIENT_EVIDENCE` yields `verdict == INSUFFICIENT_EVIDENCE` with the token stripped from `text`. (b) `answer(q, [])` returns `INSUFFICIENT_EVIDENCE` with **zero** LLM calls. (c) A provider response with `stop_reason == "refusal"` yields `PROVIDER_REFUSED`, **not** `INSUFFICIENT_EVIDENCE`, and reading `content` is never attempted (Key decision 5). (d) `stop_reason == "max_tokens"` yields `TRUNCATED`. (e) A missing or malformed verdict line yields `ERROR` rather than a silently-assumed `ANSWERED`.
- **AC-7 (streaming)** — `stream_answer` emits `VerdictEvent` **first**, before any `TextDelta`; `CitationEvent`s interleave at resolved markers; `AnswerComplete` is last and carries usage, cost, and latency. Concatenating every `TextDelta` equals the non-streaming `Answer.text` for the same stubbed response. A stream that ends without usage (simulated disconnect) still produces `AnswerComplete` with a `TRUNCATED`-or-`ERROR` verdict and whatever token counts arrived — never a silently-zero cost.
- **AC-7a (the verdict token never reaches the client)** *(added after review)* — the verdict line is buffered until the first newline and is **not** forwarded as text. Asserted at the byte level, because a partial leak is the realistic failure: with a provider that streams `"ANSW"`, `"ERED\nArticle 6"`, no emitted `TextDelta` contains any prefix of `ANSWERED` or `INSUFFICIENT_EVIDENCE`, and the first `TextDelta` begins at the first character *after* the newline. Covered for every split point of the verdict line (after each character of the token, and between the token and its newline), plus: a verdict line with trailing spaces before the newline; a stream whose **first** chunk already contains the newline and body together; and a stream that ends mid-verdict with no newline at all, which must yield `ERROR` and emit **zero** `TextDelta` rather than leaking the partial token as prose.
- **AC-8 (query_log + migration 0004)** — `alembic upgrade head` adds `verdict`, `answer_text`, `prompt_version` to `query_log`; `downgrade -1` removes them; both exit 0. One completed `answer()` with a session factory writes exactly one row whose `provider`/`model` match the client's (`anthropic`/`claude-sonnet-5`, derived from `identity` — Key decision 8), whose `retrieved_chunk_ids` equal the input chunk ids in order, and whose `verdict`, `answer_text`, `prompt_version`, `latency_ms`, `prompt_tokens`, `completion_tokens`, and `cost_usd` are all populated. A refused call writes a row too.
- **AC-9 (cost + pricing table)** — `cost_usd` equals `prompt_tokens × input_rate + completion_tokens × output_rate` at the identity's point-in-time rate, to 6 decimal places (matching `numeric(10,6)`). Constructing a client whose identity is absent from the pricing table raises at **construction time**, not at request time (Key decision 10). The Sonnet 5 introductory-rate expiry is represented as data with a date: a unit test asserts $2/$10 per MTok is selected for a timestamp on 2026-08-31 and $3/$15 for one on 2026-09-01. Each rate row carries the date it was verified and the documentation URL it came from. **Added 2026-07-26:** the `UnknownModelError` message names the pricing page **for that identity's provider** — an `openai:` identity names OpenAI's page and not Anthropic's, asserted in both directions; and a test pins that no `openai:` rate row ships *and* that the README documents the requirement, so adding a row without updating the note fails (Key decision 10, amended).
- **AC-9a (cost recomputation across a rate change — Key decision 16)** *(added 2026-07-26, second review)* — recomputation resolves the rate from the request's own `created_at`:
  - **Boundary, asserted on both sides:** identical token counts recomputed for `created_at = 2026-08-31T23:59:59Z` and `2026-09-01T00:00:01Z` yield **different** costs, matching the introductory and standard Sonnet 5 rows respectively (1 MTok in + 0.1 MTok out → $3.000000 and $4.500000). Two seconds apart, two rates.
  - **Purity:** the same logged row recomputes to the same cost whether recomputed before or after the change — and **not** to what today's-rate pricing would give. This is the assertion that fails if anyone reintroduces `datetime.now()` into the recomputation path.
  - **Across stored rows:** two `query_log` rows written with `created_at` straddling 2026-09-01 are read back and repriced from their own timestamps, producing the two different rates; the batch total differs from the same rows priced at a single current rate. Run against the database, so the `timestamptz` round-trip is part of the assertion rather than assumed.
  - **Live round-trip:** a request logged now reprices from its stored `created_at` to **exactly** the `cost_usd` that was stored — proving the request was priced at the same timestamp that was written, not at two independently-sampled clocks.
  - **Naive timestamps raise:** `recompute_cost` with a naive `datetime` raises `ValueError` rather than assuming UTC.

- **Cross-spec note (binding on SPEC-007) — citation precision is a separate metric from groundedness, and it is the one that catches this spec's known weaknesses** *(added after review)*. The two are easy to conflate and measure different failures:
  - **Groundedness** asks: is this claim supported *somewhere* in the retrieved set? It is the standard RAG faithfulness metric and it is necessary — but it **passes a mis-numbered citation**, because the supporting text is present, just not where the marker points.
  - **Citation precision** asks: does marker `[n]` point to the chunk that supports *that specific sentence*? Scored per marker, not per answer.

  **Citation precision is the metric that catches the two costs this spec knowingly accepts.** Key decision 1 accepts that inline markers are model-authored, so a model can attach a wrong number to a true sentence and nothing in the generation path detects it — groundedness will not catch that, by construction. Key decision 12's breadcrumb analysis identifies a sharper version: an overlap chunk labelled "Article 6" whose body contains trailing Article 7 text produces a mis-citation *with a correct-looking section path*, which passes both groundedness and a casual human read. **Two requirements follow:** SPEC-007 scores citation precision separately from groundedness and reports both; and the judge resolves each marker to the chunk **body**, never the breadcrumb (Key decision 12), or it will systematically validate exactly these errors.

- **Cross-spec note (binding on SPEC-007) — Opus 5 vs. Sonnet 5 on the golden set, recorded as a measured finding** *(added after review)*. Key decision 15 defaults to `claude-sonnet-5` on cost plus an argument about task shape, **not on evidence**. SPEC-007 runs the golden set against both `anthropic:claude-sonnet-5` and `anthropic:claude-opus-5` — same prompt version, same retrieved chunks, same rubric — and records answer correctness, refusal correctness (both directions: wrongly answering an unanswerable question, and wrongly refusing an answerable one), and citation precision per model, alongside measured cost and latency per question. The output is a recorded comparison in the eval artifacts, not a verbal impression. **Until that runs, the Sonnet 5 default is an assumption and must be described as one** — including in the README.
- **AC-10 (generator identity)** — `Answer.generator_identity` and the `query_log` row come from `client.identity`, never a module constant: asserted by constructing `AnthropicClient(model="claude-sonnet-5")` and observing `anthropic:claude-sonnet-5` end-to-end. A fake client with a distinct identity is likewise recorded verbatim.
- **AC-11 (HTTP surface) — superseded by SPEC-006** *(amended 2026-07-26, in SPEC-006's implementation commit)*. This spec originally owned `POST /ask` in `rag_qa/generation/api.py`. **SPEC-006 owns the HTTP layer and renames the endpoint to `POST /query`**, with no alias: nothing outside this repository depended on the path, and two names for one operation is carrying cost, not compatibility (SPEC-006 KD-2). `rag_qa/generation/api.py` is removed and its coverage moves to `tests/test_api_*.py`. **What this spec still requires, now asserted there:** the endpoint returns 200 with answer, verdict, and citations for a normal question; `stream: true` returns an event stream whose events match AC-7's ordering; `EmbedderMismatchError` and `EmptyCorpusError` surface as 503 naming the cause; and a blank question is 422. SPEC-006 AC-4 additionally pins the rule this spec's design implies but never stated: **every verdict returns 200**, because refusal is a scored capability and a 4xx would encode it as a failure.
- **AC-12 (prompt is versioned and inspectable)** — `PROMPT_VERSION` is non-empty and appears on every logged row; changing `SYSTEM_PROMPT` without changing `PROMPT_VERSION` fails a test that pins the prompt's sha256 against its declared version. This makes prompt drift a build failure rather than an archaeology problem.

## Test plan

`tests/test_generation_prompt.py`, `test_generation_citations.py`, `test_generation_clients.py`, `test_generation_service.py` (plus `tests/test_api_*.py`, which SPEC-006 owns and where `test_generation_api.py`'s coverage moved) — async where DB-touching, reusing SPEC-002's binding fixture pattern and SPEC-004's committed-and-cleaned-by-id seeding where real chunks are needed.

**No network in any tier.** Unlike SPEC-004's quality tests, generation has no measurement that requires a live provider at this stage — correctness here is about prompt shape, parsing, verdicts, and logging, all of which are better tested against a controllable fake. Answer *quality* is SPEC-007's job and is where live calls belong.

- **Fake clients (the workhorse).** `FakeLLMClient` returns a scripted response with a settable `identity`, `stop`, and token counts, and records the exact `system`/`user` strings it was called with — that recording backs AC-3, AC-4, and AC-12. A streaming variant yields text in deliberately awkward slices, including one that **splits a citation marker across chunk boundaries** and one that ends without a usage event.
- **Citation parser tests are pure** (AC-5) — no DB, no client. Property-style coverage of the marker grammar: markers at string start/end, adjacent markers `[1][2]`, a bare `[` at end of stream, `[0]`, `[999]`, and a marker straddling every possible split point of a short response.
- **Provider adapters (AC-2, AC-3)** are tested against captured request payloads and synthesized provider-shaped responses — Anthropic's `input_tokens`/`output_tokens` and `stop_reason` values, OpenAI's `prompt_tokens`/`completion_tokens` — asserting both normalize to the same `LLMResult`. The Anthropic refusal path (AC-6c) is exercised with a `stop_reason: "refusal"` response carrying **empty content**, which is what makes an unguarded `content[0]` read raise.
- **Service + `query_log` tests** (AC-8, AC-9, AC-9a, AC-10) run against the dockerized Postgres. Migration 0004 follows SPEC-002's scratch-database pattern, seeding a pre-migration `query_log` row to prove the new not-null columns are added without data loss. AC-9a's boundary cases need no clock manipulation and no frozen time: `recompute_cost` is a pure function of `created_at`, so rows are simply written with the timestamps under test — which is itself the property being asserted.
- **API tests** (AC-11, now SPEC-006's) use `httpx.ASGITransport` against an app built by `create_app()` with a stubbed retriever and fake client — no database, no provider.

Tests are written from these ACs and committed with the implementation, in the same commit series referencing SPEC-005.
