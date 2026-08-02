# SPEC-007 — Evaluation harness

**Status:** Draft — awaiting review by the repository owner

**Date:** 2026-08-02
**Depends on:** SPEC-002 (`eval_runs` / `eval_results`), SPEC-003 (corpus, de-saturation gate), SPEC-004 (retrieval, tuning metric), SPEC-005 (generation)
**Consumed by:** SPEC-009 (the explanatory panel renders this spec's report)

**One prerequisite is binding and currently unmet.** SPEC-004 Key decision 12a:
*do not report retrieval metrics, tune fusion, or set quality floors against a
corpus that has not passed SPEC-003 AC-10 (de-saturation).* At 358 chunks
recall@3 and recall@8 are pinned at 1.000 for both methods, which makes this
harness **unfalsifiable as scoped**. Corpus expansion is a prerequisite, not an
enhancement (Key decision 7).

**Both amendments this draft proposed are now approved and applied**
*(2026-08-02)*: `query_log.source` (SPEC-002 migration 0005) and the
source-scoped daily window (SPEC-006 Key decision 16, amendment 7). The spend
path they define has been exercised end to end against a stub provider — see
the Test plan. **This spec itself remains Draft**: the harness, the artifacts,
and the capture mechanism below are not implemented.

## Purpose

Make the project's central claim checkable by someone who does not trust it.

Everything else here produces an answer; this produces a number about how good
those answers are, and the number is the thesis. That places an unusual
obligation on it: **a figure this harness publishes is read by people who cannot
inspect how it was produced**, because SPEC-006 Key decision 16 binds SPEC-009 to
render the eval report in the explanatory panel — to visitors, on the demo's
worst day, beside labeled recordings. At that moment the figure is a public
claim about quality made by an artifact the reader cannot audit, and a number
that overstates what it knows converts an honest project into a misleading one
at exactly the point it is being evaluated.

Three things, in order:

1. **Score answers against a golden set**, refusals included, recording every run
   in `eval_runs` / `eval_results` so a result is attributable to a corpus state,
   a commit, and a configuration.
2. **Adjudicate the retrieval questions the repository has deliberately left
   open** — first among them whether Reciprocal Rank Fusion beats vector-only,
   measured 2026-07-26 as a *loss* on paraphrase questions and held un-tuned
   since, waiting for this.
3. **Publish a report whose every figure carries its warrant and its
   methodology**, in the artifact itself, so the panel renders both together.

## Non-goals

- **No LLM-as-judge in v1.** It replaces a measurement problem with a second,
  unmeasured model. Revisit only with a human-labelled agreement study.
- **No leaderboard or public benchmark comparison.** This corpus and question set
  are ours; a number from them is not comparable to anyone else's, and presenting
  it as if it were is the overstatement this spec exists to prevent.
- **No tuning inside the harness.** It measures; a person changes the system and
  re-measures. A harness that searches the configuration space optimises against
  its own test set by construction.
- **No online or per-request evaluation.** SPEC-006's Non-goals already put this
  offline against the libraries.
- **No CI regression gate in v1** (Key decision 8) — it needs a floor, and a
  floor cannot be set before the corpus is de-saturated.
- **No claim that this set is unbiased.** It is single-author and cannot be
  blind. That is stated as a bound, not mitigated away — Key decision 3.

## Interface

```
evals/
  golden/
    answerable.jsonl        # authored cases, with provenance
    unanswerable.jsonl
  retrieval/
    cases.jsonl             # SPEC-004 AC-6a's separate, larger, generation-free set
  reports/
    report-<git-sha>-<chunks>chunks-<utc-date>.json   # immutable
    latest.json                                        # convenience copy
  recordings/
    recordings-<git-sha>-<chunks>chunks-<utc-date>.json  # immutable; KD-11
contracts/
  eval-report.schema.json       # GENERATED + drift-checked, like conditions.json
  eval-recordings.schema.json   # ditto — SPEC-009 binds to this shape
```

**A captured recording** — the *mechanism*'s output. Which of these a visitor
sees, and how the label is rendered, is SPEC-009's (KD-11):

```jsonc
{
  "case_id": "eu-ai-act-art6-2",
  "question": "What does Article 6(2) classify as high-risk?",
  "answer": "…",
  "verdict": "answered",
  "citations": [ /* CitationOut shape, so the panel renders them like a live answer */ ],
  "captured_at": "2026-09-14T11:02:44Z",
  "git_sha": "…",
  "corpus_chunks": 1180,
  "prompt_version": "v1",
  "from_run_id": "…"          // the same run that produced the report beside it
}
```

```bash
uv run python -m rag_qa.evals.run --set retrieval              # free; no generation
uv run python -m rag_qa.evals.run --set golden                 # estimates, spends nothing
uv run python -m rag_qa.evals.run --set golden --spend         # the billable call
uv run python -m rag_qa.evals.report <run-id>                  # render only
```

**Two sets, not interchangeable** (SPEC-004 AC-6a, binding):

| | golden | retrieval |
|---|---|---|
| Size | 50 (predates any power analysis — KD-6) | as large as authoring allows |
| Calls the model | yes — real money (KD-5) | **no** |
| Measures | verdict, citation correctness, refusal | recall@k, MRR@k |
| Runs | deliberately, by a person | freely, once de-saturated |

**A case** — provenance is part of the record, not a comment:

```jsonc
{
  "case_id": "eu-ai-act-art6-2",
  "question": "What does Article 6(2) classify as high-risk?",
  "kind": "answerable",                    // or "unanswerable"
  "expected_sections": ["EU AI Act › CHAPTER III › Article 6"],
  "authored_from": "eu-ai-act",            // the document, before any retrieval ran
  "authored_before_retrieval": true,       // KD-3; false is allowed and is reported
  "edited_after_seeing_results": false,    // KD-3; true invalidates the case
  "unanswerable_verified_at": null,        // required when kind == "unanswerable"
  "notes": "near-miss: the 10-K risk factors use similar language"
}
```

**A published figure** — never a bare scalar:

```jsonc
{
  "metric": "refusal_rate_on_unanswerable",
  "value": 0.86,
  "n": 22,                                 // the denominator, always
  "decided": 22,                           // cases where it could have gone either way
  "interval": [0.65, 0.97],                // Wilson, 95%
  "corpus_chunks": 1180,
  "git_sha": "…",
  "claim": "On 22 questions verified unanswerable against this corpus, the system declined 19.",
  "not_a_claim": "That it declines 86% of unanswerable questions in general."
}
```

**A comparison figure** — the primary analysis, and it needs its own shape.
Key decision 10 makes RRF-vs-vector-only the first job and Key decision 12
pre-registers McNemar's exact test; the scalar figure above has nowhere to put a
discordant count, a *p*, or which arm won, so a report echoing a pre-registered
test could not carry that test's result. **The comparison would have been the one
number in the report without a warrant** — the exact defect Key decision 1 exists
to prevent, sitting on the primary analysis:

```jsonc
{
  "kind": "comparison",
  "metric": "recall@8",
  "arms": {"hybrid": 0.9231, "vector_only": 0.8846},
  "b": 18,                       // McNemar: hybrid succeeds, vector-only fails
  "c": 7,                        // vector-only succeeds, hybrid fails
  "n_discordant": 25,            // b + c; the pre-registered informativeness gate
  "n": 130,                      // every paired case, discordant or not
  "test": "mcnemar-exact",       // pinned by the preregistration block
  "sidedness": "two-sided",
  "alpha": 0.05,
  "p": 0.0433,
  "outcome": "hybrid",           // "hybrid" | "vector_only" | "inconclusive"
  "corpus_chunks": 1180,
  "git_sha": "…",
  "prompt_version": "v1",
  "claim": "On 130 paired questions, hybrid and vector-only disagreed on 25; hybrid won 18 of those (McNemar exact, two-sided, p = 0.043).",
  "not_a_claim": "That hybrid retrieval is better in general, or by this margin on any other corpus."
}
```

**`outcome` is `inconclusive` whenever `p >= alpha` or `n_discordant` is below
the pre-registered threshold**, and `inconclusive` is a result rather than a
missing one — SPEC-004 Key decision 12's whole correction was that a 2–1 split of
three decided questions had been reported as a win. A comparison that cannot name
an arm must say so in `outcome`, not omit the field and let the reader infer from
`arms`.

**The report carries its own methodology**, because SPEC-009 renders it to a
reader who has not opened the repository:

```jsonc
{
  "schema_version": "1",
  "methodology": {
    "summary": "50 questions authored from the source documents before retrieval was run…",
    "authoring": "single-author, not blind — see limitations",
    "limitations": ["…"],                  // KD-3's bound, rendered, not filed
    "corpus": {
      "chunks": 1180, "documents": [...],
      // DERIVED, not asserted: `desaturated` is computed from the measurement
      // beside it against SPEC-003 AC-10's gate. A producer that simply declares
      // itself de-saturated is grading its own prerequisite.
      "recall_at_8_retrieval_set": 0.94,
      "desaturated": true            // == (recall_at_8_retrieval_set < 1.000)
    },
    "preregistration": {                   // KD-12, echoed verbatim from the spec
      "preregistered_at": "2026-08-02",
      "primary_metric": "recall@8", "k": 8,
      "lever": "corpus growth",
      "test": "mcnemar-exact", "sidedness": "two-sided", "alpha": 0.05,
      "informative_when": "recall@8 < 1.000 AND discordant_pairs >= 25"
    },
    "deviations": [                        // empty is only valid if nothing differs
      {"field": "…", "preregistered": "…", "actual": "…", "reason": "…"}
    ]
  },
  "figures": [ /* scalar and comparison shapes above */ ],
  "cost_usd": "0.4831",
  // On the report, not only on each recording: a figure has to be reproducible
  // from its own artifact, and the prompt that produced the answers is part of
  // what produced the number.
  "prompt_version": "v1",
  "generator_identity": "anthropic:claude-sonnet-5",
  "run": {"id": "…", "git_sha": "…", "dirty_worktree": false, "created_at": "…"}
}
```

## Key decisions

1. **Every published figure carries its warrant, and `claim` / `not_a_claim` are
   required fields rather than documentation.** The failure mode is not
   fabrication — it is a true number licensing a false inference. **The
   repository already has the worked example:** SPEC-004 AC-6 asserted hybrid
   recall@1 > vector-only, measured 0.929 vs 0.857, and that margin was *one net
   question out of fourteen* from a 2–1 split of three decided questions. It was
   written up as evidence hybrid retrieval works. It was a coin flip, and KD-12
   had to be amended to withdraw the claim while keeping the assertion. **The
   number was never wrong; the inference was** — which is precisely why `n`,
   `decided`, and an interval are structural rather than editorial. `n` is the
   denominator, never the corpus size; `decided` is the count of cases where the
   metric could have come out either way; `not_a_claim` states the
   generalisation the figure does **not** support.

2. **The methodology travels inside the artifact, because the reader is in a
   browser and not in the repository.** A `METHODOLOGY.md` in the repo is
   sufficient for a reviewer and useless to the visitor SPEC-009 is showing the
   panel to, and "the details are on GitHub" is how a caveat gets separated from
   the number it qualifies. So the report embeds `methodology.summary`,
   `methodology.authoring`, and `methodology.limitations`, and **SPEC-009 renders
   the limitations adjacent to the figures rather than behind a disclosure** — a
   caveat one click away from a number is a caveat most readers never see.

3. **The eval set is authored by the same person who built the retriever, this
   cannot be fixed here, and the honest response is to name it and bound it.**
   *This is the decision most likely to be criticised, and it should be.* The
   failure mode is not dishonesty; it is that one mental model produced both
   artifacts, so the questions are unconsciously written in the vocabulary the
   chunker preserved, against the sections the breadcrumb design surfaces,
   phrased the way the embedder likes. The set then measures the system against
   its own assumptions and returns a flattering number that is entirely
   true and entirely uninformative. Four concrete mechanisms, each with the countermeasure
   actually available to a solo project:

   | Mechanism | Countermeasure |
   |---|---|
   | Questions authored while looking at chunk boundaries | Author from the **source document**, before ingestion output is consulted; `authored_from` records which |
   | A question rewritten after seeing a bad result | `edited_after_seeing_results: true` **invalidates** the case — it must be re-issued under a new `case_id`, not repaired |
   | Clean sections chosen, messy ones (tables, cross-references) avoided | SPEC-003's cross-spec note already mandates near-miss and multi-source authoring; the report publishes the composition |
   | Unanswerable questions chosen to be *obviously* unanswerable | Unanswerability is verified by retrieval (KD-4), and the report publishes the near-miss share |

   **The bound, stated because it cannot be removed:** none of this makes the set
   blind, and a single-author eval set systematically overstates the system it
   was written for. The report says so in `methodology.limitations`, in the panel,
   in those words. **What would actually fix it** — questions authored by someone
   who has not seen the retrieval code, or drawn from an external benchmark — is
   named as the revisit condition rather than pretended at.

4. **Unanswerability is a claim about the corpus, verified by retrieval, not
   asserted by the author** (SPEC-003's cross-spec note, binding). With
   overlapping regulatory material a question authored as unanswerable from one
   document may be answerable from another. `unanswerable_verified_at` records
   the corpus state the verification ran against, and **a case whose verification
   predates the current corpus is not scored** — it is reported as stale, loudly.
   Silently scoring it turns a corpus expansion into a fake refusal failure or,
   worse, a fake success.

5. **An eval run spends the demo's budget, and the first draft of this spec said
   it did not. Corrected here.** *(CLAUDE.md rule 7 — third instance, and the
   first found in a Draft.)* The draft claimed "SPEC-006's ceiling does not apply
   — this runs offline against the libraries, not through the API." **That is
   false, and the mechanism is one line:** `Generator._write_query_log` writes a
   `query_log` row whenever it holds a `session_factory`, `SpendGuard` sums
   `cost_usd` over **every** row in the window, and `query_log` had **no column
   distinguishing traffic**. The ceiling is not an API-layer feature that offline
   code escapes; it reads a ledger the *library* writes. Running offline changed
   nothing.

   **The arithmetic, which is why this mattered more than a wording fix.** Fifty
   golden questions at realistic usage is **~$0.65** against a derived daily
   ceiling of **$0.64** ($20/month ÷ 31). One run exhausts the day's visitor
   budget, and a run started near the ceiling trips it — taking the demo down, on
   a schedule, to measure how good the demo is.

   **Settled by SPEC-002 migration 0005 and SPEC-006 Key decision 16 amendment
   7** *(both approved 2026-08-02)*: `query_log.source` discriminates the
   traffic, the **daily** window filters to `visitor`, and the **monthly** cap
   counts every source because it is the invoice. An eval run therefore writes
   `source = 'eval'` rows — its cost is in the ledger, visible to the invoice,
   and invisible to the demo's burst limit. **The interim this draft proposed —
   running with `session_factory=None` and noting the gap in the report — is
   withdrawn**: it would have put eval spend outside the invoice, and the place
   to qualify "the monthly cap is the invoice" is the Key decision that makes
   that claim, not the report of a spec that inherits it. SPEC-006 Key decision
   16 now carries that scope, with its own removal condition.

   **A run reserves; it does not check.** The first version of this decision had
   the runner compare its estimate against remaining headroom at startup, and
   called that "the reservation idea at a coarser grain". It was not — **it was
   check-then-spend, which is the design SPEC-006 Key decision 16 amendment 5
   exists to replace.** A scheduled run and a manual one begun minutes apart both
   check, both pass, and neither sees the other until it finishes. So the runner
   uses the mechanism that already exists:

   - **Reserve the run's worst case at start** — `per-question worst case ×
     len(cases)`, from `Generator.max_cost`, tagged `SpendSource.EVAL`. Measured
     at 50 questions: **$0.0449 per question, $2.24 for the run** (~3.5× the
     ~$0.65 actual, the same conservatism the per-request bound has, held for the
     length of a run rather than a call).
   - **Settle to actual at the end**, so the over-reservation lasts one run.
   - **Release on every exit path**, via `Reservation`'s context-manager form
     whose `__exit__` fires on `BaseException` — because the exception an eval
     run will actually meet is `KeyboardInterrupt`, which is not an `Exception`,
     and which someone will produce after watching the first few results.
     `SIGKILL` is out of scope and needs no handling: the claim lives in the
     process.
   - **A run that cannot reserve does not start**, and says which ceiling
     refused it. A scheduled eval on the 28th of an expensive month declines
     rather than being the thing that exhausts the month.

   **The reservation is per-run, not per-question, and that is not an oversight
   waiting to be optimised.** The arithmetic looks indefensible: **$3.86 held to
   spend $0.65**, a 5.9× over-claim, held for the length of a run against a $20
   month — 19 % of the month's headroom, unavailable, to spend 3 %. Reserving
   per question would hold ~$0.077 at a time and look obviously better. It is
   worse, and the reason is not about money:

   - **A per-question reservation converts "refuse to start" into "stop at
     question 34".** The refusal does not go away; it moves to the middle of the
     run, where it produces a *partial artifact* — 33 answers, a report whose
     denominator is 33, and a recordings file missing whatever came after.
   - **A partial measurement is worse than none, because its denominator is
     chosen by budget exhaustion.** The cases are in **authored order**, not
     random order, so "the first 33 questions" is a subset selected by when the
     author happened to write them — the same authoring bias Key decision 3
     spends a table fighting, re-entering through the metric instead of through
     the case. A refusal rate over that subset is a number with a warrant that
     `not_a_claim` cannot honestly state.
   - **What is actually being conserved is availability, not money.** The
     reserved amount is not spent; it is unavailable for the minutes the run
     takes. The optimisation trades a real correctness property for a brief
     accounting nicety.
   - **If the 5.9× ever does bite** — a month tight enough that a run cannot
     reserve — the correct responses are to raise the budget, wait for the
     window, or shrink the *question set deliberately and say so in the report*.
     They are not to make the run interruptible. **This paragraph exists so that
     optimisation is rejected on sight rather than rediscovered.**

6. **50 golden questions is sized for failure-mode coverage, and is explicitly
   not sized for significance** *(restated 2026-08-02: the first version called
   it "undefended", which was the wrong word — it has a job and a stated
   non-job)*. SPEC-004 Key decision 12a routes statistical power to the
   retrieval-only set; the golden set's job is that **every failure mode this
   system has is exercised often enough that one case does not dominate its
   fraction.** The allocation:

   | Failure mode | Cases | Why that many |
   |---|---|---|
   | Citation-exact ("Article 6(2)") | 12 | The terms-of-art case hybrid retrieval exists for; one failure is 8 % |
   | Paraphrase | 12 | Where RRF was measured *losing* on 2026-07-26 — the open question needs a populated cell |
   | Multi-source (answer spans two documents) | 8 | Possible only since SPEC-003's corpus expansion; the mode most likely to produce a wrong-but-plausible answer |
   | Near-miss (answerable, with a competing document) | 6 | Distinguishes "retrieved the right document" from "retrieved *a* plausible document" |
   | Unanswerable | 12 | Refusal is a scored capability; the 2×2's declined-correctly cell needs a denominator that is not a handful |
   | **Total** | **50** | |

   **Twelve is the floor, and it is chosen from what one case is worth:** at 12,
   a single case moves the mode's fraction by 8.3 points, which is legible as
   one case rather than as a trend. Below ~10 the fraction becomes noise wearing
   a percentage. **The non-job stands unchanged:** this set cannot support a
   significance claim between two configurations, and the report must not quote
   one. If config comparison later becomes the point, the fix is a power
   calculation before authoring more — not more questions in the same shape.

7. **The corpus must pass SPEC-003 AC-10 before this reports anything — inherited
   from SPEC-004 KD-12a, not chosen here.** Reporting against a saturated corpus
   produces figures that are true, stable, and meaningless: the exact thing KD-1
   exists to prevent, arriving through the corpus instead of the arithmetic.
   Enforced with a named failure (AC-9), not documented — a binding constraint
   living only in prose is one someone follows until they are in a hurry.

8. **No CI regression gate in v1, because a floor cannot be set honestly yet.**
   SPEC-004 AC-6 declined an absolute floor on the grounds that "a provisional
   number amended to match the first run would be a measurement wearing a
   standard's clothes", and that holds harder here, where a gate makes the number
   load-bearing for merges. **Revisit when** the corpus is de-saturated and two
   consecutive runs at the same corpus state have established run-to-run
   variance; the floor then sits below observed variance, not at the last run.

9. **Every guarantee this spec makes about quality carries a test or a stated
   bound** (CLAUDE.md rule 7, applied to the spec most exposed to it). A quality
   claim is the one kind of claim in this repository that reaches strangers, so
   the rule is enforced mechanically rather than by review: AC-1 makes an
   unwarranted figure unrenderable, and **AC-10 asserts that this document
   contains no unqualified quality claim** — every sentence asserting the system
   is good either names the figure that supports it or names its bound.

10. **The RRF-vs-vector-only question is this harness's first job, and
    "vector-only wins" is an allowed answer.** Measured 2026-07-26: plain RRF
    loses to vector-only overall on paraphrase questions. Nothing has been tuned
    since, deliberately — tuning before a harness exists means tuning against a
    number nobody can reproduce. CLAUDE.md's hybrid-retrieval rationale is a
    hypothesis this measures, not a commitment it defends; if hybrid loses on a
    de-saturated corpus, the finding is published and the stack decision is
    revisited by amendment.

11. **This spec owns the *capture mechanism* for the demo's pre-recorded Q&A
    pairs; SPEC-009 owns which pairs are shown, how they are labeled, and how
    they render** *(added 2026-08-02, owner review)*. The recordings SPEC-006 Key
    decision 16 binds into the explanatory panel had no owning spec, which is how
    a required content of a panel ends up produced by nobody. **The split is
    capture versus curation**, and it falls where the machinery already is:

    | This spec | SPEC-009 |
    |---|---|
    | Runs the `Generator`, tags `source='eval'`, reserves and settles | Chooses which captured pairs appear |
    | Writes an immutable, schema-checked artifact | Labels them as recordings, with the capture date |
    | Guards it against drift and overwrite | Decides layout, ordering, and adjacency to the figures |

    **The point of the split is that one paid run produces both artifacts.** The
    corpus, the question set, the de-saturation gate, the reservation, and the
    demo-down window are the same for a report and for a set of recordings, so
    capturing them separately would mean two paid runs, two lead times, and two
    occasions on which the day's visitor budget is consumed. One
    `source='eval'` run emits `evals/reports/report-….json` **and**
    `evals/recordings/recordings-….json`, from the same answers, at the same
    corpus state and git sha — which additionally means a recording shown beside
    a figure is a recording *of the run that produced that figure*, rather than
    two artifacts a reader has to be trusted not to compare.

    **What this spec deliberately does not decide:** which questions are
    interesting enough to show a visitor, and how "recorded" is rendered so the
    label is not a styling detail. Those are presentation, they are SPEC-009's,
    and SPEC-006 Key decision 16 already binds the honesty requirement onto that
    spec rather than this one.

    **The seam is closed with validity as the boundary, so "whose bug" has one
    answer** *(added 2026-08-02, owner review)*. A split where each side assumes
    the other did its job produces the failure both sides can disclaim. So:
    **this spec validates every recording against
    `contracts/eval-recordings.schema.json` at capture time and refuses to emit
    the artifact if any record fails** (AC-15). **SPEC-009 may assume a valid
    artifact** — it is entitled to render `citations[0].section_path` without
    checking it exists. **Anything invalid is this spec's defect by definition**,
    including a schema-valid record whose contents are wrong (an empty answer, a
    citation pointing at no chunk), because the validation is where "wrong" is
    supposed to be caught. The corollary binds too: SPEC-009 must not add
    defensive re-validation, because a consumer that re-checks is a consumer that
    will eventually diverge on what "valid" means, and then there are two
    definitions and no owner.

12. **The de-saturation target is pre-registered here, before any de-saturation
    work, and a run reporting something else must show it as a deviation**
    *(added 2026-08-02, owner review)*. Key decision 3 makes
    `edited_after_seeing_results` invalidate a *case*; this is the same bias one
    level up, operating on the *metric*. `recall@8` is 1.000 for both methods, so
    de-saturating means changing the corpus, `k`, or the questions — and choosing
    which lever **after** seeing which one separates the methods is choosing the
    measurement to fit the result. Nothing in this spec forbade it until now.

    **The pre-registration, stated as values so a diff shows any change:**

    ```
    preregistered_at:     2026-08-02
    primary_metric:       recall@8          # SPEC-004 AC-6a, inherited not chosen here
    k:                    8                 # the API default; held fixed
    diagnostic_metrics:   MRR@8, recall@{1,3}, discordant-pair counts
    lever:                corpus growth     # SPEC-003 AC-10's measured rungs
    levers_held_fixed:    k, the question set, the chunker config, the embedder
    comparison:           hybrid (RRF) vs vector-only, same query embeddings
    pairing_unit:         one question; discordant = exactly one method succeeds at k=8
    test:                 McNemar's test, exact binomial form (no continuity
                          correction, no mid-p adjustment)
    sidedness:            two-sided
    alpha:                0.05
    informative_when:     recall@8 < 1.000  AND  discordant_pairs >= 25
    ```

    **The test and its sidedness are pinned because they move the answer, and
    the first version of this block pinned neither** *(corrected 2026-08-02,
    owner review)*. At 25 discordant pairs an 18–7 split gives **p = 0.0216
    one-sided, 0.0433 two-sided** — the same data landing either side of 0.05 at
    a 20–5 split's neighbours, decided by a parameter nobody had written down. A
    block that fixes the metric and the threshold but leaves the test open can
    move a result across the line after the fact, which is the thing
    pre-registration exists to prevent.

    **Two-sided, and one-sided would not be defensible here.** A one-sided test
    encodes the alternative hypothesis in the instrument: it can reject only in
    the pre-chosen direction, so choosing "hybrid > vector-only" would make the
    measurement unable to report the finding the repository *already has
    evidence for* — RRF measured as a **loss** on paraphrase questions on
    2026-07-26. Key decision 10 states that "vector-only wins" is an allowed
    answer; a one-sided test in the hybrid's favour would contradict that in the
    arithmetic while the prose kept saying it. One-sided is defensible when the
    opposite direction is genuinely uninteresting or impossible — not when it is
    the outcome the last measurement pointed at. **Named as McNemar's exact test
    rather than described**, so the choice is reviewable by someone who will not
    redo the binomial.

    **`discordant_pairs >= 25` is a claim about the set's power, not about the
    result.** SPEC-004 Key decision 12 established discordant pairs as the honest
    instrument after recall@1's "win" turned out to be a 2–1 split of three
    decided questions. Below 25, no split the set can produce reaches α = 0.05
    two-sided, so a run clearing `recall@8 < 1.000` but not this threshold has
    de-saturated the corpus without making the comparison answerable. **The
    threshold says what the set must be able to detect. It does not predict,
    require, or prefer a hybrid win.**

    **Deviations are visible or the run is invalid.** The report's `methodology`
    carries a `preregistration` block echoing these values and a `deviations`
    array. A run whose primary metric, `k`, or lever differs from the block must
    carry a deviation entry with a reason; a report where they differ and
    `deviations` is empty **fails to render** (AC-14). Changing the target is
    allowed — discovering that `recall@8` cannot be de-saturated by corpus growth
    alone is a legitimate finding — but it is allowed *out loud*, in a diff, with
    a date, and not by quietly reporting MRR@8 instead because that is the one
    that moved.

## Acceptance criteria

- **AC-1 (no bare scalar is publishable)** — Every figure carries `n`, `decided`,
  an interval, `corpus_chunks`, `git_sha`, `claim`, and `not_a_claim`; a report
  containing a figure missing any of them fails to render, asserted by
  constructing one. The renderer has **no code path** that emits a value without
  its warrant — asserted structurally, since the failure is an omission and
  checking figures that happen to be well-formed cannot prove it.
- **AC-2 (the warrant is not decorative)** — Reconstructing SPEC-004's
  2026-07-26 measurement as a report (recall@1 0.929 vs 0.857, three decided
  questions) yields `decided: 3`, an interval spanning the difference, and a
  `not_a_claim` stating it is not evidence hybrid retrieval works. **The
  historical over-claim is the fixture**: if this harness would have published
  that result as evidence, the field set is wrong. It is the only case in this
  repository where an over-claim actually happened, so it is the only fixture
  that tests the guard against something other than the author's imagination.
- **AC-3 (refusal is scored as a 2×2, not an accuracy)** — A run reports all four
  cells: answered-correctly, answered-when-it-should-have-declined, declined-
  correctly, declined-when-it-could-have-answered. Asserted with two stub
  generators — one that never refuses, one that always does — producing visibly
  different reports. A single accuracy figure would let both land on the same
  number and make the entire refusal design invisible to the metric meant to
  justify it.
- **AC-4 (a stale unanswerable case is not scored)** — A case whose
  `unanswerable_verified_at` predates the current corpus state is excluded and
  reported as stale. Asserted by moving the corpus state forward and re-running,
  and asserted that it is scored **neither** as a pass nor as a fail.
- **AC-5 (authoring provenance is enforced, not requested)** — A case with
  `edited_after_seeing_results: true` is **rejected by the loader**, naming the
  case id and the rule; a case missing `authored_from` is rejected. The
  proportion authored before retrieval appears in `methodology`, so a set that
  drifts toward post-hoc authoring is visible in its own report.
- **AC-6 (the methodology reaches the reader)** — `methodology.limitations` is
  non-empty and contains the single-author bound in words, asserted on the
  rendered report rather than on the config that produced it. A report whose
  limitations array is empty fails to render.
- **AC-7 (a run is attributable, and reports are immutable)** — Every
  `eval_results` row joins an `eval_runs` row carrying `git_sha`,
  `dataset_name`, `config`, and the corpus chunk count; a dirty worktree is
  recorded as such. A second run at the same git sha and corpus state does not
  overwrite the first report — the write guard fails the run, reusing SPEC-004
  AC-13's mechanism rather than a second one.
- **AC-8 (spend is explicit, estimated, bounded, and attributed)** —
  `--set retrieval` makes **zero** provider generation calls, asserted by
  counting calls on a fake client. `--set golden` without `--spend` makes zero
  calls and prints an estimate. A golden run whose estimate exceeds the remaining
  **monthly** headroom refuses to start, naming the shortfall (KD-5). The actual
  cost is a field on the report, and the report states whether that cost is
  present in `query_log` or absent from it.
- **AC-9 (the de-saturation prerequisite is enforced)** — Reporting against a
  corpus that has not passed SPEC-003 AC-10 fails with a named cause rather than
  producing figures.
- **AC-10 (every quality claim in this spec is a reviewed one — inverted 2026-08-02)** — The first version was a regex over prose asserting that no unqualified claim exists. **A pattern-match over English produces false positives, gets suppressed, and a suppressed test is worse than none** — it reports a clean bill from a rule nobody runs. Inverted to the same regenerate-and-compare shape as `contracts/conditions.json`: `scripts/export_spec_claims.py` extracts every claim-shaped sentence from this document **together with its qualifier** into `contracts/spec-007-claims.json`, and the test regenerates and fails on any difference. Adding a claim sentence therefore fails the build, and **the fix is to regenerate, which puts the sentence and its qualifier into a reviewable diff** — the review happens on the artifact rather than in a matcher's confidence. A claim that reaches the file without a qualifier is visible in that diff; one that never reaches the file cannot exist, because the extractor is what the test compares against.
- **AC-15 (the recording seam has one owner — Key decision 11)** *(added 2026-08-02)* — Every recording is validated against `contracts/eval-recordings.schema.json` **at capture**, and a run with any invalid record emits **no** recordings artifact rather than a partial one. Asserted by injecting a malformed record. The complementary assertion is on SPEC-009's side and is named here so neither spec adds it twice: a consumer that re-validates is out of contract, because two definitions of "valid" is the seam this closes.
- **AC-16 (the comparison figure carries the pre-registered test's result)** *(added 2026-08-02)* — A comparison figure carries `arms`, `b`, `c`, `n_discordant`, `n`, `test`, `sidedness`, `alpha`, `p`, and `outcome`, and its `test`/`sidedness`/`alpha` **equal the `preregistration` block's** — a report whose comparison disagrees with its own pre-registration fails to render unless a deviation is recorded (AC-14). `outcome` is `inconclusive` whenever `p >= alpha` **or** `n_discordant` is below the pre-registered threshold, asserted directly with a 2–1 split of three discordant pairs — SPEC-004's original over-claim, which must come out `inconclusive` here. A comparison figure missing any field fails to render, like any other figure (AC-1).
- **AC-17 (a report is reproducible from itself)** *(added 2026-08-02)* — The report carries `prompt_version` and `generator_identity` at the top level, not only on recordings; `methodology.corpus.desaturated` is **derived** from `recall_at_8_retrieval_set` beside it rather than asserted, and a report whose `desaturated` disagrees with that measurement fails to render. A producer that declares itself de-saturated is grading its own prerequisite.
- **AC-12 (one run, two artifacts, one corpus state)** — A single run emits both
  the report and the recordings, and every recording's `git_sha`,
  `corpus_chunks`, and `from_run_id` match the report's. Asserted by construction
  rather than by convention: a recording captured at a different corpus state
  from the figure it is displayed beside is a comparison a reader would make and
  be misled by. `evals/recordings/` is immutable under the same write guard as
  reports and baselines.
- **AC-13 (the whole artifact path runs against a stub before it runs for
  money)** — Capture → both artifacts → schema validation → drift check
  completes end to end with a stub provider and zero provider calls, asserted in
  CI. The failure this prevents is discovering a malformed artifact *after* the
  budget is spent and the demo has been taken down to produce it, at which point
  the fix costs a second run and a second demo-down window. **The paid run is not
  the first exercise of this path.**
- **AC-14 (a deviation from the pre-registration is visible or the report is invalid — Key decision 12)** *(added 2026-08-02)* — The report embeds a `preregistration` block whose values match Key decision 12's, asserted against the spec text so the two cannot drift. A run whose `primary_metric`, `k`, or `lever` differs from that block and whose `deviations` array is empty **fails to render**, naming the field that differs. A run that differs *and* records a deviation with a reason renders, and the deviation appears in the panel beside the figure — a substitution the reader cannot see is the failure this criterion exists to prevent, and hiding it in the repo is the same failure with an extra step.
- **AC-11 (the report contract cannot drift)** — `contracts/eval-report.schema.json`
  is generated from the report model and regenerated in the test, failing on any
  difference — the same guard `contracts/conditions.json` gets, for the same
  reason: SPEC-009 binds to this shape, and a consumer reading a stale schema
  fails silently in the direction of rendering nothing.

## Test plan

`tests/test_evals_*.py`. The harness's own tests use **stub generators and the
seeded synthetic corpus** — never the real corpus, never a provider. A test suite
that spends money to run is a test suite that stops being run.

**The shape to be most suspicious of here is a report test asserting on the
report object rather than the rendered report** (CLAUDE.md rule 3, and the sixth
and seventh entries in its list). AC-1 and AC-6 are therefore written against
rendered output, and AC-1's structural half is about the absence of a code path,
because "no figure lacks a warrant" cannot be proved by inspecting figures that
have one.

**Every acceptance criterion is verified by breaking the behaviour it covers.**
The mutations that matter: emit a figure without `n`; score a stale unanswerable
case instead of excluding it; accept a case edited after its results were seen;
let the retrieval set call the generator; let a golden run start with insufficient
monthly headroom; let a second report overwrite the first; empty the limitations
array; remove the de-saturation check.

**AC-3 needs two stub generators rather than one**, and that is the point of it:
a metric that cannot separate never-refuses from always-refuses is the metric
this criterion rejects, and a single stub cannot demonstrate separation.
