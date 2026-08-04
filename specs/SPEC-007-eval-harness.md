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
      "preregistration_id": "prereg-2",
      "corpus_adequacy": "recall@8 < 1.000, with a recorded headroom judgment",
      "conclusive_when": "n_discordant >= 6 AND p < alpha",
      "otherwise": "inconclusive",
      "retrieval_set_size": 120,
      "assumed_discordance": 0.05
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
                          # ^ WITHDRAWN, unsatisfiable — see amendment 1 below.
                          #   Superseded by prereg-2's corpus_adequacy +
                          #   conclusive_when. Left visible rather than deleted:
                          #   a pre-registration that quietly loses a line is
                          #   indistinguishable from one that never had it.
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

    **~~`discordant_pairs >= 25` is a claim about the set's power, not about the
    result.~~ WITHDRAWN — the threshold was unreachable and its justification was
    arithmetically false. See amendment 1 below.** SPEC-004 Key decision 12
    established discordant pairs as the honest instrument after recall@1's "win"
    turned out to be a 2–1 split of three decided questions; that part stands.

    ---

    ### Amendment 1 — the pre-registration was unsatisfiable, and is replaced *(2026-08-02, owner-asked after Rung 0; CLAUDE.md rule 4's owner-asked clause)*

    **Declared as a deviation before the fetch, not after a result.** Nothing has
    been ingested, no rung has moved, and the run that would have been judged
    against this block has not happened. That ordering is the entire difference
    between a deviation and a result-fitted substitution, and it is why this is
    written now rather than when a number came out wrong.

    **Finding 1 — the threshold is unreachable by construction.** Discordant
    pairs are bounded above by the question count. The retrieval set is **26**
    and the threshold is **25**, so clearing it requires the two methods to
    disagree on **96 %** of questions — a rate no corpus produces, and one that
    would itself indicate something broken rather than something measured. And
    `questions` sits in `levers_held_fixed`, so the block **forbids the only
    lever that could make its own threshold reachable**. Corpus growth cannot fix
    it at any rung: the ladder can drive `recall@8` below 1.000, but it cannot
    raise a bound that is set by the size of the question set.

    **Finding 2 — the justification given for 25 was arithmetically false**, and
    it is worth recording because it is the reason the number survived review.
    The withdrawn sentence claimed "below 25, no split the set can produce
    reaches α = 0.05 two-sided." Under the exact binomial McNemar this block
    pins, that is wrong at both ends: a **unanimous 6–0** split gives
    **p = 0.03125**, and a **20–5** split gives **p = 0.0041**. The smallest `n`
    at which *any* split can reject is **6**, not 25. A threshold that is
    unreachable is a bug; a threshold that is unreachable *and* defended by a
    false claim about the test is the pattern CLAUDE.md rule 7 exists for, and
    this is its fourth instance — a formula written in the indicative, checked by
    nobody, decaying in the direction that flattered the design.

    **Finding 3, and the one that matters most — the block asked a corpus gate to
    guarantee a result.** `informative_when` conjoined two unrelated things: a
    property of the corpus (`recall@8 < 1.000`) and a property of the *outcome*
    (enough discordance for significance). **No corpus can guarantee that a
    comparison will come out conclusive** — that is what running the comparison
    is for. Conflating them produced a gate that could never open and, worse, one
    whose closure would have been read as "the corpus is not ready" when the true
    reading was "the instrument cannot answer this question at this set size."

    **This lever was named in advance, in two places, before this block existed.**
    That record is what makes the present change a deviation rather than a
    substitution chosen to fit Rung 0:
    - **SPEC-003 AC-10** *(2026-07-26)*: "**Decided-question counts are recorded
      but are explicitly NOT part of the stop condition** … Short decided-counts
      are answered by SPEC-004's retrieval-only eval set, **never by another
      rung**."
    - **SPEC-004's cross-spec note** *(2026-07-26)*: "Sizing is derived, not
      chosen: `N ≈ required_decided_pairs / observed_decision_rate` … **No
      question count is fixed here**", and, separately, the warning that a round
      number already written down must not be "quietly reinterpreted as the
      instrument for retrieval significance."

      **So the error was not an oversight about which lever to use — it was
      overriding a decision two approved specs had already made.** KD-12, written
      2026-08-02, froze `questions` after both of those said the question set was
      precisely the lever that answers short decided-counts. The pre-registration
      contradicted its own dependencies and nothing caught it, because the block
      was reviewed for whether the test and sidedness were pinned — which they
      were — and not for whether its threshold could be reached.

    #### The choice: option (b), a threshold derived from the test and a set size I can defend

    Option (a) — size the set for 25 discordant pairs at a plausible rate —
    **requires a number that does not exist.** The only discordance figure this
    repository has measured is **7/26 ≈ 0.27, and it is at k = 1 on a saturated
    corpus**: a different statistic, at a different `k`, under exactly the corpus
    conditions the sizing is meant to escape. Discordance falls as `k` rises,
    because both methods get eight chances instead of one, and **nothing here
    measures by how much**. Sizing `N = 25 / r` from that would put a decimal
    point on a guess and call it a derivation — which is what 25 was.

    **Option (b) is chosen. The decisive structural difference: under (b) a wrong
    rate assumption costs *power*, not *satisfiability*.** The threshold comes
    from the test itself and is reachable at any set size; only the probability of
    reaching it depends on the rate. When the assumption is wrong the run reports
    **inconclusive**, which is a defensible outcome. Under (a) a wrong rate
    reproduces exactly the failure being amended.

    **The threshold is derived, not chosen: `n_discordant >= 6`.** Below 6, the
    pre-registered test is *mathematically incapable* of rejecting at α = 0.05
    two-sided even on a unanimous split (`2 × 2⁻ⁿ ≥ 0.05` for `n ≤ 5`). Nothing
    about the corpus, the questions, or the effect size enters this number; it
    falls out of the exact binomial and α alone, which is precisely why it cannot
    be tuned toward a desired answer.

    **The set size, and its basis stated as an assumption rather than smuggled as
    a fact.** `N = 120`, derived as `6 / 0.05` — the structural floor at the
    **pessimistic** planning rate. The rate is a **stated assumption with no
    measurement behind it**: `r = 0.05` is roughly one-fifth of the measured k = 1
    rate of 0.27, on the reasoning that a top-8 window forgives most rank-1
    disagreements. **Nothing verifies that ratio.** It is written here so it can
    be checked against the first de-saturated measurement and corrected, and it
    sizes toward the floor at a pessimistic rate rather than toward significance
    at an optimistic one.

    **The power this buys, stated honestly including where it is bad.** θ is
    `P(hybrid wins | the pair is discordant)`; power is computed under the exact
    binomial at α = 0.05 two-sided:

    | Discordance rate `r` | Expected `n` at N = 120 | Power, θ = 0.7 | θ = 0.8 | θ = 0.9 |
    |---|---:|---:|---:|---:|
    | 0.27 (the k = 1 measurement, optimistic) | 32 | 0.61 | 0.95 | 1.00 |
    | 0.15 | 18 | ~0.38 | ~0.75 | ~0.97 |
    | 0.10 | 12 | 0.19 | 0.48 | 0.85 |
    | **0.05 (the planning assumption)** | **6** | **0.12** | **0.26** | **0.53** |

    **Read that bottom row plainly: at the rate this set was sized against, the
    comparison is badly underpowered and will usually come out inconclusive.**
    N = 120 is well powered only if discordance lands at or above ~0.15 *and* the
    effect is large. **This is stated as a limitation of the instrument, not as a
    prediction about the answer**, and the honest outcome is written into the
    contract below rather than discovered later. An inconclusive result that can
    be defended is worth more than a threshold that was never reachable.

    **Authoring cost, since it is the real constraint and it is a person's time,
    not money.** 26 questions exist; 94 must be authored and human-verified. At
    the ~2–3 minutes per label SPEC-004 estimates, that is **4–5 hours of owner
    time**, spread over the ladder rather than paid at once. **This is the one
    number in this amendment most worth pushing back on**: raising N raises every
    power figure above, and the table is the exchange rate.

    #### The replacement pre-registration

    ```
    preregistration_id:   prereg-2                    # prereg-1 = 2026-08-02, superseded
    preregistered_at:     2026-08-02
    supersedes:           prereg-1 (unsatisfiable; see amendment 1)
    primary_metric:       recall@8          # SPEC-004 AC-6a, inherited not chosen here
    k:                    8
    diagnostic_metrics:   MRR@8, recall@{1,3}, discordant-pair counts
    lever:                corpus growth     # SPEC-003 AC-10's measured rungs
    levers_held_fixed:    k, the chunker config, the embedder,
                          the question set — WITHIN a pre-registration
    retrieval_set_size:   120               # 26 existing + 94 to author
    assumed_discordance:  0.05              # ASSUMPTION, unmeasured; basis above
    comparison:           hybrid (RRF) vs vector-only, same query embeddings
    pairing_unit:         one question; discordant = exactly one method succeeds at k=8
    test:                 McNemar's test, exact binomial form (no continuity
                          correction, no mid-p adjustment)
    sidedness:            two-sided
    alpha:                0.05
    corpus_adequacy:      recall@8 < 1.000, with a recorded headroom judgment
                          (SPEC-003 AC-10). NO discordance term.
    conclusive_when:      n_discordant >= 6  AND  p < alpha
    otherwise:            INCONCLUSIVE — reported as inconclusive, never as
                          "no difference" and never as a corpus failure
    ```

    **The two gates are separated, and that is the substantive fix.** Corpus
    adequacy is a property of the corpus and belongs to SPEC-003 AC-10, which
    already owns it and correctly carries no discordance term. Conclusiveness is a
    property of the *result* and is therefore **not a gate at all** — it is an
    outcome, recorded either way. Nothing in this spec now asks a corpus to
    promise that a comparison will succeed.

    #### The general rule, which is what finding 3 was an instance of

    **A pre-registration whose stop condition contains a term about the result
    can only be satisfied by getting the answer you wanted.** Not "is more likely
    to be" — *only*. The gate opens when the result comes out one particular way
    and stays shut otherwise, so the design is incapable of reporting the other
    answer at all. What it reports instead is "not ready to measure", which reads
    as a statement about the instrument and is in fact a statement about the
    finding it declined to have.

    **The test, and it is mechanical: for every term in a pre-registration, ask
    whether it could be evaluated the day before the run.** Corpus size, `k`,
    question count, chunker config, embedder identity, α, the test, its sidedness
    — all yes, all legitimate. A *p*-value, a discordant count, a recall figure
    from the run being gated, "the comparison is answerable" — all no. Anything
    in the second list is a finding wearing a pre-condition's clothes.

    **Where a quantity is genuinely needed for planning but is only knowable
    afterwards, it does not go in the pre-registration at all — it goes in a
    separate sizing study whose output feeds one.** This is the legitimate home
    for the discordance rate `r`, and the difference is not bureaucratic: a
    sizing study is allowed to look at data because it makes no claim, and its
    cases are excluded from the analysis it sizes precisely so that looking
    cannot contaminate anything. A pre-registration that swallows the sizing
    question instead has to *guess* the quantity — which is how `assumed_
    discordance: 0.05` got into prereg-2 — and a guessed planning constant is the
    same defect one level down from a guessed threshold.

    **Why this belongs as a rule rather than a note about KD-12.** The failure is
    not that 25 was too big. A threshold of 3 would have been reachable and just
    as wrong, because the defect is *categorical*: the condition referred to the
    outcome. A rule stated as "25 was unreachable" invites the fix "use a smaller
    number", which preserves the error. Stated as "no term about the result", it
    forbids the whole class.

    **`questions` is frozen WITHIN a pre-registration, not forever, and the
    distinction is load-bearing.** Freezing it across the rungs of one ladder run
    is what makes those rungs comparable; freezing it across all time is what made
    the threshold unreachable. Changing the set opens a **new pre-registration
    id** and every rung already measured must be **re-measured against the new
    set** before it can be compared to a later one. The machinery already enforces
    this: each baseline artifact carries a sha256 of the question set, and
    `tests/test_baseline_artifacts.py::test_the_frozen_levers_agree_across_every_rung`
    fails when two artifacts in the same pre-registration disagree on it.

    ---

    ### Amendment 2 — the pilot sizing study, pre-registered before authoring *(2026-08-02, owner-asked)*

    **Written and committed before a single pilot question exists.** The git
    history is the evidence and it is the whole point: a sizing study whose
    design is recorded after its data would size nothing.

    **Why a pilot at all — it removes the assumption that made option (a)
    unacceptable.** Amendment 1 rejected sizing `N` from a rate because the rate
    did not exist, then carried `assumed_discordance: 0.05` as a planning
    constant. That is the same defect at lower stakes and it should not survive.
    A sizing study is the legitimate way to obtain `r`: it is allowed to look at
    data because it makes no claim, and its cases are excluded from the analysis
    it sizes so that looking cannot contaminate anything.

    **It also answers a prior question that would make the sizing moot.** The
    Rung 1 probe (SPEC-003, 2026-08-02) found 683 added chunks moving `recall@8`
    by nothing and only 4 of 26 gold ranks moving at all — which points at the
    *question set* rather than the corpus. If deliberately hard questions
    de-saturate at 358 chunks, the corpus ladder is not the lever and SPEC-003
    Key decision 13 is wrong about more than Tier 1.

    ```
    pilot_id:            pilot-1
    preregistered_at:    2026-08-02
    purpose:             SIZING STUDY. Not a quality measurement.
    size:                12–15 questions
    corpus:              the existing 358 chunks, unchanged. Nothing is fetched.
    k:                   8
    authoring_rule:      written from corpus text WITHOUT an expected section in
                         hand. The gold label is determined by verification after
                         the question exists, never chosen before it.
    target_shapes:       (a) the answer spans two sections
                         (b) the obvious lexical match is the wrong chunk
                         (c) near-miss — a plausible section does not contain the
                             answer
    measures:            recall@8 (hybrid, vector-only), discordant pairs, and r
                         = discordant / n
    excluded_from:       the confirmatory set, permanently and by id.
    reports:             N = 6 / r_measured, with the power table recomputed at
                         the measured rate.
    ```

    **Three bounds on what the pilot's number will be worth, stated now so they
    cannot be added or dropped after seeing it:**

    1. **No figure from the pilot is a quality result.** At n = 12–15 a recall
       figure has an interval wide enough to contain almost anything. The pilot
       reports `r` and a direction; it licenses no claim about how good retrieval
       is, and none of its numbers may appear in the published report.
    2. **`r` is measured *for this authoring recipe*, not for questions in
       general.** The recipe deliberately targets hard shapes, so `r` from it is
       an upper estimate for a naturally-authored set. **If the confirmatory set
       is authored to a different recipe, `r` does not transfer and must be
       re-measured** — and if it is authored to the same recipe, that recipe is
       itself now a pre-registered design choice rather than a preference.
    3. **The labels are machine-drafted and human-unverified until the owner
       confirms them.** SPEC-004's cross-spec note binds: *"the label is ground
       truth and is never machine-accepted — an auto-labeled retrieval set
       measures the labeler, not the retriever."* Every `r` this study produces
       is provisional on that verification, and the questions are presented for
       review with their gold sections and the reason each was labelled as it
       was.

    ---

    ### Amendment 3 — pilot-2, the fourth question shape, pre-registered before authoring *(2026-08-02, owner-asked)*

    **Committed before a single pilot-2 question exists**, same discipline as amendment 2.

    **Why this shape and not more of the other two.** Pilot-1's `r = 0` has two live explanations and the existing sets cannot separate them, because each occupies one branch of a vise: citation-style questions make the full-text branch fire but leave `recall@8` saturated, so no discordant pair is possible; natural-language questions de-saturate `recall@8` but silence the branch, so hybrid *is* vector-only and no discordant pair is possible. **The cell neither set occupies — hard *and* lexically anchored — is the only one where the branch can fire while recall is not saturated, and therefore the only shape that can produce a discordant pair at k = 8.**

    ```
    pilot_id:            pilot-2
    preregistered_at:    2026-08-02
    purpose:             DIAGNOSTIC first, sizing second.
    size:                12–15 questions
    corpus:              the existing 358 chunks, unchanged. Nothing is fetched.
    k:                   8
    cell:                hard AND lexically anchored
    authoring_rule:      written from corpus text WITHOUT an expected section in
                         hand; gold determined by verification after the question
                         exists. Additionally: every question must contain at
                         least one term of art or citation appearing VERBATIM in
                         the corpus, and must avoid vocabulary the corpus does not
                         contain, so that websearch_to_tsquery's conjunction is
                         satisfiable.
    target_shapes:       as pilot-1 — wrong-lexical-match, near-miss, spans-two-sections
    excluded_from:       the confirmatory set, permanently and by id
    ```

    **The decision rule, fixed before the run, with all three outcomes named:**

    | Outcome | Reading |
    |---|---|
    | The branch fires on a majority **and** discordant pairs appear | It works for its intended case. Pilot-1's 13/14 silence is a fact about **question style**, and SPEC-004 AC-12(b) is a **scoping** question, not a bug. |
    | The branch returns zero **even here** | The ANDing is a **defect independent of question style**. AC-12(b) is a bug. |
    | The branch fires **but discordance is still 0** | Neither explanation fits. **Report it as a third outcome rather than forcing it into either branch** — this row exists so that a surprise is recorded as a surprise. |

    **The asymmetry, stated in advance because it governs how much the result is worth.** Pilot-2 is a **best case for the branch**, constructed by constraining vocabulary to what the corpus contains. **Failure here is decisive; success is weak** — it would show the branch works when queried in a register a user may never use, and the API accepts sentences. A "works" result therefore licenses no claim about production behaviour, only about the mechanism.

    ---

    ### Amendment 4 — the confirmatory set's shape mix, chosen a priori *(2026-08-04, owner decision)*

    **This is an owner decision, not a measurement.** The mix cannot come from the data: picking the recipe with the larger `r` is the substitution this key decision exists to prevent, one level up from choosing the threshold. **So it is chosen on product grounds — the mix mirrors what the system will actually receive — and whatever `r` follows is accepted, including an `r` that leaves the comparison underpowered and the result inconclusive.**

    **Committed before authoring. No sentence in the justification below refers to discordance, `r`, or any pilot result.**

    | Shape | Share | What it is |
    |---|---:|---|
    | **Natural-language** | **70 %** | A question typed as an ordinary English sentence, no citation, answer in one section |
    | **Citation-anchored** | **15 %** | The asker names the instrument or article: "Article 6", "Item 1A", "the Govern function" |
    | **Cross-section** | **15 %** | An ordinary question whose answer is genuinely spread across two sections |

    **Why 70 % natural-language.** The frontend is a text box and `/query` accepts a string; SPEC-009 sends sentences and users type sentences. **This is the modal input by construction**, not by estimate — every visitor who does not know the corpus produces one, and a public demo's visitors mostly do not know the corpus. A set that under-weights it measures a system other than the one being shipped.

    **Why 15 % citation-anchored.** The corpus is three *named*, article-structured instruments, and a compliance reader who arrives already knowing the AI Act will type "Article 6" because that is how the instrument is navigated. But this is a public demo linked from a README, not an internal compliance tool: **most visitors will not know an article number to cite.** Roughly one in seven is the share I would defend as arriving with that knowledge — high enough that the shape is real, low enough that it is not the system's centre of gravity.

    **Why 15 % cross-section.** Not because anyone *intends* to ask a spanning question, but because **compliance obligations are cross-referenced by design**: Article 26 sends the reader to Articles 13, 49 and 72; Annex IV to Chapter III Section 2; the AI RMF's Core functions reference each other explicitly. A question asked in complete good faith lands on a spread answer often enough to be a real minority of traffic. 15 % is deliberately conservative — it says "a real minority", not "common".

    **Why the remaining categories are absent.** *Unanswerable* questions belong to the golden set, not here (SPEC-004's division of labor: the retrieval set measures recall and MRR against section labels, with no generation and no judge). *Wrong-lexical-match* and *near-miss* were **difficulty devices used to build the pilots**, not shapes a user chooses; **the confirmatory set is authored to be representative, and whatever difficulty results is the system's actual difficulty.** Engineering difficulty into a confirmatory set is the same act as engineering it out.

    **The authoring rule is unchanged from the pilots** — written from corpus text without an expected section in hand, gold determined by verification after the question exists — with one addition: **the shape is assigned before the question is written**, so the mix is a design input rather than a description of what happened to get written.

    **Exclusions, by id:** every `pil-*` and `anc-*` case. ~~**Proposed, requiring the owner's call:**~~ **APPROVED 2026-08-04 (amendment 5).** SPEC-004's cross-spec note calls the existing 26-question smoke set *"the seed of the retrieval set"*. It is **not** part of the confirmatory set — every one of those questions was authored with its expected section in hand, which is the property pilot-1 identified as making them lexical bullseyes. They are retained as a **regression** set.

    #### Sizing against that mix — the owner picks N

    **The mix was fixed first; this arithmetic follows it.** Using measured `r` is legitimate *here* and would not have been legitimate for choosing the shares.

    Discordant pairs required, from the exact binomial at α = 0.05 two-sided, θ = 0.8:
    ~~**floor (rejection possible at all) = 6 · power 0.50 = 12 · power 0.80 = 20.**~~ **CORRECTED in amendment 5 to 6 · 15 · 23** — power is not monotone in `n` for a discrete test, and 12 and 20 are first crossings it falls back below.

    `r` for the mix is a weighted sum of per-shape rates. **All three scenarios are extrapolations and the middle one is not more likely than the others** — the pilots were authored to be *hard* and the smoke set was authored with its answer in hand, so the confirmatory set sits between two anchors that both miss it in known directions.

    > **SUPERSEDED by amendment 5 on both axes** — the required counts were wrong (12/20 → 15/23) and the low scenario's `r` was not derivable from anything. The table is kept as written because it is the table the owner chose N against.

    | Scenario | `r` (mix) | N at floor | N at power 0.5 | **N at power 0.8** |
    |---|---:|---:|---:|---:|
    | ~~**Low** — smoke-like, questions land easily~~ | ~~0.042~~ | ~~145~~ | ~~289~~ | ~~**481**~~ |
    | ~~**Mid** — blend~~ | ~~0.175~~ | ~~35~~ | ~~69~~ | ~~**114**~~ |
    | ~~**High** — pilot-like, questions land hard~~ | ~~0.322~~ | ~~19~~ | ~~38~~ | ~~**63**~~ |

    **Authoring cost, at 2.5 min per question (SPEC-004 estimates 2–3 min; a retrieval question needs a verified *label*, not a verified answer):**

    | N | At 2.5 min | At 4 min |
    |---:|---:|---:|
    | 50 | 2.1 h | 3.3 h |
    | 100 | 4.2 h | 6.7 h |
    | 150 | 6.2 h | 10.0 h |
    | 200 | 8.3 h | 13.3 h |
    | 300 | 12.5 h | 20.0 h |

    **What an afternoon buys, stated plainly.** ~100 questions is four hours and covers power 0.8 *only if* `r` lands at or above the mid scenario; at the low scenario 100 questions does not even reach the floor. **The honest framing is that N buys a chance at a conclusive answer, not a conclusive answer** — and per this amendment, an inconclusive result on a representative set is the accepted outcome, not a failure of the set.

    **The recommendation is N = 150.** It clears the floor under every scenario including the low one (145), reaches power 0.8 under mid and high, and costs 6–10 hours. **It does not rescue the low scenario's power**, and no affordable N does — 481 questions is twenty hours, which is not an afternoon and would be the largest single artifact in the repository.

    **`r` is re-measured on the first 30 authored questions and reported before the remainder is written.** If it lands near the low anchor, the choice between "author 481" and "accept inconclusive" should be made with 1.25 hours spent rather than 6.

    > ~~**This is not a stopping rule that can change the analysis** — the target metric, test, sidedness, α and floor are all fixed above; only how many questions get written, and that decision is the owner's.~~
    >
    > **WITHDRAWN 2026-08-04 as insufficient (amendment 5).** The conclusion stands; the argument for it does not, and the argument is the part that has to be right. **Adaptive sizing can inflate Type I error with metric, test, sidedness, α and floor all pinned** — that list is equally true of an unblinded interim look, which is exactly the unsafe case. The correct reason is that `r` is a *nuisance parameter*, and it is written out in amendment 5 with the constraint it implies.

    ---

    ### Amendment 5 — N, the smoke set, and the blinded interim *(2026-08-04, owner decision)*

    Three owner decisions, and a correction to the reasoning under one of them.

    #### 1. The smoke set does not seed the confirmatory set — approved

    The Proposed marker in amendment 4 comes off. **26 of 150 is 17 % of a representative set, and every one of those 26 was authored with its expected section in hand** — the property pilot-1 identified as the cause of the saturation that made `recall@8` uninformative in the first place. Importing it to save four hours of authoring would put a known bias in a sixth of the instrument.

    The smoke set is **retained as a regression set**, which is what it is good at: 26 cases with stable expected sections, cheap to re-run, and sensitive to exactly the ranking changes that broke AC-6. **SPEC-004's cross-spec note calling it "the seed of the retrieval set" is superseded by this amendment**, and is annotated there rather than deleted.

    #### 2. N = 150 — approved, with the reason the hours are justified

    Recorded because "N = 150" standing alone reads as a default, and a default is the thing nobody defends later:

    - **Vector-only winning is a deletion.** It removes the full-text branch, the second concurrent connection SPEC-002 KD-5 exists to provide, the RRF fusion step, and the generated `tsvector` column — plus the OR fallback and the pruning toggle that now hang off them. That is a smaller system with fewer moving parts and one fewer index to maintain.
    - **Hybrid winning is a verification.** It establishes the claim this architecture has rested on, untested, since SPEC-001 — the one CLAUDE.md's stack rationale is scoped under rule 7 for making without evidence.
    - **Inconclusive is the only outcome that pays nothing**, and it is the outcome N buys down. The hours are not buying a preferred answer; they are buying against the answer that leaves every decision exactly where it is.

    #### 3. The interim at 30 — the correct justification, and the constraint it implies

    **What was wrong with the withdrawn argument.** "Metric, test, sidedness, α and floor are fixed" does not establish that a data-dependent sample size is safe. **Adaptive sizing can inflate Type I error with all of those pinned** — the standard case is re-estimating N from an interim look at the *effect*, where the continuation decision becomes correlated with the test statistic. Every word of the withdrawn sentence is equally true of that unsafe design, so it distinguishes nothing. **It would have licensed an unblinded re-estimation**, and the whole safety of this interim lies in one property it never mentioned.

    **The correct reason: `r` is a nuisance parameter, and this is a blinded internal pilot.** McNemar's exact test **conditions on** `n = b + c`; conditional on `n`, the statistic is `b`, and under H₀ `b ~ Binomial(n, ½)`. The interim estimates `r`, which is a rate of `n` — **a quantity the test conditions on carries no information about the statistic it conditions toward.** If the sizing rule is a function of the discordance *indicators* only, then under H₀ each discordant pair's direction remains an independent fair coin independent of which questions were discordant, so conditional on the *final* `n` the null distribution of `b` is unchanged and the test's size is preserved. Being a discrete exact test, the achieved size is ≤ α rather than = α; adaptivity of this kind does not move it upward.

    **The assumption that carries it, stated so it can be checked:** the sizing decision must depend on the discordance indicators and on nothing else. **The moment `b` and `c` are inspected separately, the argument above is void** — not weakened, void, because the continuation decision is then a function of the statistic.

    **The constraint, therefore: the split is sealed until the full set runs.** The interim reports `n_discordant` and nothing from which the direction can be reconstructed. Seeing 8–1 at question 30 and choosing to continue **is** peeking at the effect, whatever is said about the reason for continuing.

    **Enforced in the tooling rather than by a note asking nobody to look** (`scripts/interim_r.py`, AC-17):

    | Mechanism | What it forecloses |
    |---|---|
    | Discordance is computed as `hit(hybrid) != hit(vector)`. **No expression on the interim path asks which side is true.** | The split is never materialised, so it cannot be printed by accident or added by a later edit that "just needed one more field". |
    | The artifact carries **no per-case ranks** and no per-arm recall. The measured ranks are discarded, not stored; the full run re-measures from scratch. | Reconstruction from the artifact, which is how a sealed summary usually leaks. |
    | Top-level and per-case keys are asserted against an **allowlist**, not a denylist. | A field added later is a test failure by default. Blinding is a property that has to fail closed. |
    | The summariser is asserted **invariant under swapping the arms**: mirrored inputs must produce byte-identical output. | A summariser that leaks direction through any channel — a field, a rounding, an ordering — fails this test. |

    **Stated bound (rule 7), because this is a guarantee and it does not cover everything.** This is a property of the *reporting path*, not a cryptographic seal. The questions, the corpus and the measurement code are all in a public repository, so anyone who wants the split can deliberately re-measure it in about a minute. **What the tooling guarantees is that the split cannot be seen as a side effect of asking for the interim number** — accidental unblinding is structurally impossible; deliberate unblinding is possible and would be an act with a commit attached to it. That distinction is the whole of what is claimed.

    **One interim look, at 30.** Further looks would remain blinded and would remain safe under the same argument, but each is another opportunity to negotiate with the set, so the number is pre-committed at one. **Any additional look is a deviation and is recorded as one (AC-14).**

    **What the interim may decide, and what it may not.** It decides **N alone** — continue to 150, extend beyond it, or stop and accept an inconclusive result. It may not change the target metric, the test, the sidedness, α, the floor of 6, or the 70/15/15 mix. All six are fixed by amendments 1 and 4 and are not in scope at the interim.

    #### 4. Composition and status of the first 30

    **Drawn in the committed 70/15/15 proportions, not 30 natural-language questions first.** A block that is not the mix estimates `r` for a population the confirmatory set does not contain, which is the same error as choosing the mix from the data, arriving through the back door.

    **The rounding rule is pre-committed** so the blocks compose exactly: 15 % of 30 is 4.5, so the two minority shapes alternate across blocks of 30.

    | Block | Natural-language | Citation-anchored | Cross-section |
    |---|---:|---:|---:|
    | 1 (the interim) | 21 | 5 | 4 |
    | 2 | 21 | 4 | 5 |
    | 3 | 21 | 5 | 4 |
    | 4 | 21 | 4 | 5 |
    | 5 | 21 | 5 | 4 |
    | **Total** | **105** | **23** | **22** |
    | Target (150 × share) | 105 | 22.5 | 22.5 |

    **The 30 are retained in the final set and authored at final quality.** An internal pilot keeps its data — that is what makes it internal rather than a discarded rehearsal, and it is why the interim costs 1.25 hours rather than 1.25 wasted hours. They are not warm-up, they are not revisited after the interim, and they carry the same authoring rule as the rest: written from corpus text without an expected section in hand, gold determined by verification after the question exists, shape assigned before the question is written.

    #### 5. Two corrections to the sizing table N = 150 was chosen against

    Both were found while writing the interim tooling, because the arithmetic had to be executed rather than asserted. **Neither changes the decision** — N = 150 survives both, and one of them improves its standing — but the table the owner read was wrong in two places and the corrections are recorded before anything is authored.

    **Correction 1 — the required discordant counts are 6 / 15 / 23, not 6 / 12 / 20.** **Power is not monotone in `n` for a discrete test.** Adding a discordant pair moves the critical value in steps, and the step can cost more power than the pair buys:

    | `n` discordant | 12 | 13 | 14 | 15 | … | 20 | 21 | 22 | 23 |
    |---|---:|---:|---:|---:|---|---:|---:|---:|---:|
    | power at θ = 0.8 | **0.558** | 0.502 | **0.448** | 0.648 | | **0.804** | 0.769 | **0.733** | 0.840 |

    12 and 20 are *first crossings*. At 14 discordant pairs the power of a set sized for 0.5 is **0.448**, and at 22 the power of a set sized for 0.8 is **0.733** — so the published numbers bought a promise that one or two more discordant pairs could take back. **Sizing takes the sustained crossing**: the smallest `n` at which power reaches the target *and stays there*. Pinned by `tests/test_interim_blinding.py::test_power_is_not_monotone_in_n_so_sizing_uses_the_sustained_crossing`, which asserts the dip itself rather than only the replacements, and is mutation-verified against the first-crossing implementation.

    **Correction 2 — the low scenario's `r = 0.042` implied a lower bound on `r` that no measurement provides.** It was not reconstructible from any artifact, which is the tell: **a number in a table with no derivation is the shape rule 7 exists for**, and it went into a table the owner was asked to choose against. The three anchors are now written out with their arithmetic:

    | Input | Measured | Rate |
    |---|---|---:|
    | smoke set, 26 questions authored with the section in hand | 0 discordant | 0 → one-sided 95 % upper bound **0.1088** |
    | pilot-1, 14 hard natural-language | 4 discordant | **0.2857** |
    | pilot-2, 14 hard lexically anchored | 2 discordant | **0.1429** |
    | spanning questions pooled across both pilots | 3 of 5 | **0.600** |

    | Scenario | Composition | `r` | N floor | N p0.5 | **N p0.8** |
    |---|---|---:|---:|---:|---:|
    | **High** — questions land as hard as the pilots' | 0.70(0.2857) + 0.15(0.1429) + 0.15(0.600) | 0.3114 | 20 | 49 | **74** |
    | **Mid** — natural-language at the smoke set's bound | 0.70(0.1088) + 0.15(0.1429) + 0.15(0.600) | 0.1876 | 32 | 80 | **123** |
    | **Low** — every shape behaves like the smoke set | 0 discordant in 26 | **0** | — | — | **—** |
    | *Low, at the bound the smoke set does support* | `r ≤ 0.1088` | ≤ 0.1088 | ≥ 56 | ≥ 138 | **≥ 212** |

    **The low row is em-dashes, not 481, and the difference is the whole point.** 0 of 26 places an *upper* bound on `r` and no lower bound at all, so it yields a lower bound on N and **no finite N**. The old table's 481 read as "expensive but purchasable"; the truth is "not purchasable at any price, and that outcome is already accepted by amendment 4". **Mid is a planning figure and says so**: it blends one bound with two point estimates, none of them from a representatively-authored question.

    **What this does to N = 150.** Nothing, and slightly in its favour. It clears the floor under high (20) and mid (32) and reaches **power 0.8 under mid at 123 with 27 questions of margin** — where the superseded table put mid's power-0.8 figure at 114 against a required count that was itself too low. Under the low scenario no N suffices, which was true before the correction and is now stated as such.

    ---

    ### Amendment 6 — a single-arm difficulty proxy, banded before block 2 *(2026-08-04, owner decision)*

    **The gap this closes.** Per-block shape quotas control drift in the dimension that was committed and **nothing in difficulty**. The mechanism: over five blocks an author gets better at writing questions that discriminate between the arms. **This is not a validity problem** — McNemar is paired and heterogeneous difficulty across pairs is fine — **it is a representativeness problem**, and representativeness is the entire justification amendment 4 gave for 70/15/15. It is also tuning toward `r` through a door no rule guards, which is the same failure as choosing the mix from the data, arriving later and more slowly.

    #### The proxy is single-arm, and that is forced rather than chosen

    Publishing both arms' recall beside `n_discordant` **determines the split exactly**:

    ```
    hybrid_hits = both + b        vector_hits = both + c        n_discordant = b + c
    =>  b = (n_discordant + hybrid_hits - vector_hits) / 2
    ```

    So the hybrid arm's recall is not computed by the interim at all. **The proxy is vector-only**, which is what makes it compatible with the blinding: it says how hard the block is, and nothing about who won.

    **It is also aggregate, never per case.** A per-case vector outcome published beside a per-case discordance flag would give the split away one question at a time — a discordant case whose vector arm hit is a `c`, one whose vector arm missed is a `b`.

    #### The banded metric is MRR@8, and recall@8 was the first choice

    Block 1's vector-only `recall@8` is **0.90**. A two-sided band of any useful width runs off the end of the scale: ±0.20 puts the upper edge at 1.10, and even a noise-derived ±0.15 puts it at 1.05. **A band only one direction can ever breach is a one-sided band wearing a two-sided label** — and the saturation doing it is the same saturation that made `recall@8` uninformative on the smoke set and set this entire arc in motion. Catching it before committing the band rather than after five blocks is the only reason it is a paragraph and not an amendment.

    | | Block 1 | Banded? |
    |---|---:|---|
    | vector-only `recall@8` | 0.9000 | **No** — saturated, upper edge unreachable |
    | vector-only **`MRR@8`** | **0.6511** | **Yes, ±0.19** |
    | gold rank histogram (1…8, miss) | 15 / 6 / 3 / 0 / 1 / 2 / 0 / 0, 3 missed | Reported |

    `MRR@8` sits mid-scale, moves continuously, and sees a block whose gold chunks all slid from rank 1 to rank 6 — which `recall@8` structurally cannot.

    #### The band, derived rather than chosen, and committed before block 2's number

    Per-question reciprocal rank has **SD 0.381** across block 1, so the standard error of a 30-question mean is **0.0696** and of the difference between two blocks **0.0984**. 1.96 × that is **0.1928**, rounded **inward to 0.19** — very slightly tighter than sampling noise alone would justify, because a false alarm costs one paragraph and a missed drift costs the set's representativeness.

    - **Reference:** block 1's `MRR@8` = 0.6511, measured before the band existed and before block 2 was authored. Anchoring on a measured block rather than on a target is deliberate: the question is whether later blocks drift from the one the mix was first authored against, not whether they hit a number someone hoped for.
    - **Breach → a deviation recorded under AC-14**, naming the block, both values and what changed in the authoring. **It is a tripwire for a conversation, not a gate on authoring** — a block is not rewritten because a proxy moved.
    - **Two-sided.** A block that gets *easier* is drift too; banding only the predicted direction would assume the named mechanism is the only one there is.

    **Bound, because this is a guarantee and it does not cover everything.** With 30 questions per block this catches a **step change** and is close to blind to a **gradual slope** — five points cannot test a trend with any power, and a drift of 0.03 per block would arrive at block 5 having never breached. **At block 5 the proxy is reported across all five blocks with its direction**, as a diagnostic that the reader can weigh, not as a gate that was passed.

    #### What this does to AC-17's guarantee

    **Arm-swap invariance still holds for the discordance summary and no longer holds for the artifact**, because a single-arm proxy is by construction not invariant under swapping the arms — swapping is exactly what it measures. Replacing it with a weaker claim would be the wrong move; the claim is replaced with the *right* one, which happens to be provable:

    > The published artifact fixes `n`, `n_discordant = b + c`, and `vector_hits = both + c`. **Three equations, four unknowns** (`b`, `c`, `both`, `neither`) — so `c` is free across its whole feasible range, and every value of it produces a byte-identical artifact.

    Tested both ways: one worked instance — `(b, c) = (7, 0)` against `(4, 3)`, same `n`, same discordant cases, same vector hits, identical output — **and** a sensitivity check showing that adding the hybrid arm's recall makes the same two splits distinguishable immediately.

    #### The general rule from the sawtooth, stated where the sizing arithmetic lives

    Recorded in `scripts/mcnemar.py`'s module docstring rather than only here, so that nobody re-derives 12 next year from a different table:

    > **The first crossing is the wrong reading of any discrete power curve.** The rejection region changes in whole observations, so the critical value jumps as `n` grows and power follows a **sawtooth**. **The first crossing is a lower bound on the sustained requirement, never an upper one** — measured across θ ∈ {0.7, 0.75, 0.8, 0.9} and targets {0.5, 0.8}, it understates by 0 to 8 discordant pairs and never overstates. The error therefore has a **direction**: reading the first crossing always buys less power than the number advertises, and silently, because the arithmetic producing it is correct as far as it goes.

    The test pins the general property — non-negative gap across all eight parameter combinations — not the two numbers that happened to be wrong here.

    ---

    ### Amendment 7 — inverse sampling replaces the fixed N *(2026-08-04, owner decision)*

    **The trigger was a design error in the fixed-N framing, not an interim number.** Every sizing table in amendments 4, 5 and 6 compared a *realized* count against a required one — "150 × 0.15 = 22.5 against the 23 required" — as though `n_discordant` were `N × r`. It is not. **`n_discordant ~ Binomial(N, r)`**, with SD ≈ 4.4 at these numbers, and the comparison silently swapped a random variable for its expectation. **The correction holds whatever `r` turned out to be**, which is why it is recorded as a design error rather than as a response to block 2.

    What the fixed-N numbers actually bought, at `r` = 0.15:

    | N | E[`n_discordant`] | SD | **P(reach 23)** |
    |---:|---:|---:|---:|
    | 150 | 22.5 | 4.4 | **0.489** |
    | 160 | 24.0 | 4.5 | **0.621** |
    | 180 | 27.0 | 4.8 | **0.826** |

    **Ten questions bought thirteen points of probability while reading as though they bought power 0.8.** Reaching ~80 % probability of *achieving* power 0.8 is about N = 180, not 160. (For completeness: a fixed N = 150 has *unconditional* power 0.79 at θ = 0.8, because runs that overshoot 23 have more power than runs that fall short have less. That is a real number and it does not rescue the framing — it is an average over designs, and the set only gets run once.)

    #### The design

    > **Author until `n_discordant` = 23, capped at N = 200.**

    - **Expected cost is 23 / 0.15 ≈ 153 questions** — what was already planned — and it **delivers the count** rather than its expectation.
    - **The cap bounds the tail if `r` drops.** P(reaching 200 without 23 pairs) is 0.065 at `r` = 0.15, 0.38 at `r` = 0.12, 0.73 at `r` = 0.10. If the cap binds, the result is reported as **underpowered and inconclusive**, which amendment 4 already accepts as an outcome.

    #### Why it is valid, and why it needs no new machinery

    **The exact test conditions on `n = b + c`.** Under H₀ the direction of each discordant pair is an independent fair coin **regardless of which questions turned out to be discordant**, so a stopping rule that reads only the discordance *indicators* leaves `b ~ Binomial(n, ½)` conditional on the realized `n`. Size is preserved; being a discrete exact test it stays ≤ α.

    **The four structural blinds from AC-17 are exactly that condition**, and they were built before this design existed: the split is never computed, the artifact carries no per-case ranks, keys are an allowlist, and the published quantities leave `c` free. **Nothing new is needed — the blinding that made one interim look safe makes an arbitrary number of them safe**, because the argument never depended on the number of looks. Amendment 5's one-look limit was protecting against negotiation with the set, not against inflated α; under inverse sampling, looking *is* the design and there is nothing left to negotiate, because the rule fixes in advance what every look decides.

    **One consequence worth stating rather than discovering: the final question is always a discordant one.** That changes the distribution of `N`, not of `b` given `n`, and the analysis conditions on `n`.

    #### Evaluated at block boundaries, which is my refinement and not the owner's instruction

    Stopping mid-block would break the shape mix at the moment the set is frozen, and the mix is the thing amendment 4 chose a priori and refused to let the data touch. **So the rule is evaluated after each block**, and the realized count will be ≥ 23 rather than exactly 23.

    | | Exact stopping | **Block-boundary stopping** |
    |---|---:|---:|
    | E[N] at `r` = 0.15 | 153.3 | **164.8** |
    | Overshoot | — | **+11.5 questions** (≈ 30–45 min) |

    Overshoot only adds power. **The price is under an hour of authoring and the thing it buys is that the committed mix holds exactly at every checkpoint**, which is worth more than eleven questions.

    P(stopping at each boundary) at `r` = 0.15: N = 120 → 0.12, **N = 150 → 0.36**, N = 180 → 0.34, N = 200 → 0.11, cap binds → 0.065.

    #### Blocks now compose to the cap, exactly

    Six blocks of 30 plus a final block of 20:

    | Blocks 1–6 (30 each) | Block 7 (20) | **Total at the cap** |
    |---|---|---|
    | 126 / 27 / 27 | 14 / 3 / 3 | **140 / 30 / 30 = 200** |

    **That is 70 / 15 / 15 with no rounding residue at all** — a better property than the one it replaces, since fixed-150 landed on 105/23/22 and had to carry the half-question in its rounding rule. At every earlier boundary the mix is off by at most one question.

    ---

    #### Block 1 authored, and the blinded interim — measured 2026-08-04, artifact `evals/interim-block-1.json`

    30 questions, `evals/retrieval_confirmatory.jsonl`, composition 21 / 5 / 4 as committed and enforced by `tests/test_confirmatory_set.py`.

    **Two authoring choices amendment 4 did not cover, recorded because they were made rather than derived:**

    - **Document distribution follows corpus share.** 201 / 119 / 38 chunks is 56 / 33 / 11 %, so the block is 17 EU AI Act, 10 10-K, 3 AI RMF. This is not a traffic estimate — nobody has traffic — and it is the only rule available that is not chosen from a measurement. It is applied to the block as a whole rather than within each shape, which is why citation-anchored questions are 3 EU and 2 10-K with no AI RMF case.
    - **"Without an expected section in hand" holds more weakly here than it did for the pilots, and the difference is stated rather than glossed.** The author knows these three instruments, so for questions like the serious-incident deadline the answering article was anticipated before it was confirmed. **What was actually controlled is vocabulary**: every question is phrased as a reader would phrase it and not as the corpus phrases it — *"software that reads its employees' emotions at work"* against the corpus's *"infer emotions of a natural person in the areas of workplace"*, *"buy back stock"* against *"share repurchase authorization"*. That is the property whose absence made the smoke set a set of lexical bullseyes, and it is the one that matters for this comparison. Anticipating the article is not the same defect and pretending otherwise would make the record less useful, not more honest.

    | | Block 1 |
    |---|---:|
    | `n` | 30 |
    | **`n_discordant`** | **7** |
    | `r` | **0.2333** |
    | 95 % CI on `r` | [0.0993, 0.4228] |
    | Questions with a silent full-text branch | **0 of 30** |

    **Which anchor it is near: neither of the ones that would have forced a decision.** It sits between the mid anchor (0.1876) and the high one (0.3114), closer to high, and **nowhere near the low case**. The low case was the one that would have put the choice between "author 481" and "accept inconclusive" on the table, and the CI's lower bound of 0.0993 sits above the smoke set's whole upper bound of 0.1088 only marginally — but the point estimate is not the smoke set's population and does not behave like it.

    | | at `r` = 0.2333 | at CI low 0.0993 | at CI high 0.4228 |
    |---|---:|---:|---:|
    | N for the floor (6) | 26 | 61 | 15 |
    | N for power 0.5 (15) | 65 | 151 | 36 |
    | **N for power 0.8 (23)** | **99** | 232 | 55 |

    **N = 150 stands, and the interim's job is done by that sentence.** At the point estimate 150 questions are expected to yield ~35 discordant pairs against the 23 that power 0.8 needs; **at the pessimistic end of the interval 150 still yields ~15**, which clears the floor of 6 outright and lands exactly on the power-0.5 requirement. **No part of the 95 % interval leaves 150 unable to clear the floor**, which is the property that separates a set that can answer from one that cannot. Extending to 232 would buy power 0.8 even at the interval's floor, at another ten hours; that is not worth it against a point estimate of 99.

    **What is still not known, deliberately: `b` and `c`.** The split was never computed. Seven pairs disagreed; which arm won each is not recorded anywhere in this repository and will not be until the full 150 runs.

    **The caveat that survives the good news.** `r` was measured on questions authored in one sitting by one author. The per-block mix constraint controls **shape** drift and does nothing about **difficulty** drift, so blocks 2–5 could land easier or harder than block 1 for reasons no rule here catches. The estimate is what block 1 supports, and it is reported as such.

    ---

    #### Block 2, its difficulty proxy, and the cumulative estimate — measured 2026-08-04, artifact `evals/interim-block-2.json`

    30 questions, composition 21 / 4 / 5 as committed for block 2, document share 17 / 10 / 3, same authoring rule and same quality bar. Retained, like block 1.

    **A gold label was wrong, and prefix matching is why.** `con-056`'s gold was written as `EU AI Act › Annex I`, which prefix-matches **`Annex Ii`, `Annex Iii`, `Annex Iv` and `Annex Ix`** — five annexes scoring as one, four wrong answers counting as right, and that single question silently four times easier than every other in the set. **Nothing in the numbers afterwards would have said so**; it looks exactly like a question that was easy. Caught by the pre-flight check added with the proxy, which now refuses to spend an embedding when a prefix matches a section path **mid-word** rather than at a component break. Fixed to `EU AI Act › Annex I — ANNEX I`, and the rule is pinned by test.

    | | Block 1 | **Block 2** | Cumulative |
    |---|---:|---:|---:|
    | `n` | 30 | 30 | **60** |
    | `n_discordant` | 7 | **2** | **9** |
    | `r` | 0.2333 | **0.0667** | **0.1500** |
    | 95 % CI on `r` | [0.0993, 0.4228] | [0.0082, 0.2207] | **[0.0710, 0.2657]** |
    | Silent full-text branch | 0 / 30 | 0 / 30 | 0 / 60 |
    | vector-only `recall@8` | 0.9000 | 0.9667 | — |
    | vector-only **`MRR@8`** (banded) | 0.6511 | **0.7423** | — |

    **The proxy did not breach, and it moved in the same direction as `r`.** `MRR@8` rose by **+0.0912** against a band of ±0.19 — comfortably inside, and worth reporting precisely because it is the second number moving the same way. The vector arm found block 2's gold chunks *more easily*, and block 2 produced *fewer* discordant pairs. Two agreeing movements are not two pieces of evidence when one plausibly causes the other.

    **Is the drop in `r` real?** Fisher's exact on 7/30 against 2/30 gives **p = 0.146**. At these block sizes the difference is entirely consistent with sampling noise, and it is reported as such rather than as a finding.

    **A mechanism, named because the proxy exists to make one nameable — and it is not the one amendment 6 predicted.** Amendment 6 anticipated an author getting *better* at writing discriminating questions. What appears to have happened is the opposite: block 2 drew more heavily on **topically-titled sections** — Art 9 *Risk management system*, Art 15 *Accuracy, robustness and cybersecurity*, Art 17 *Quality management system*, Art 57 *AI regulatory sandboxes*, Art 72 *Post-market monitoring*, Art 78 *Confidentiality* — where the section title is inside the chunk text and states the question's topic. Block 1 leaned more on answers sitting in sections whose titles do **not** announce them (Art 73's deadlines, Art 26(7)'s duty to workers, Art 5(1)(f)'s workplace emotion prohibition). **A title that states the topic is easy for both arms**, which lifts the proxy and suppresses discordance together. This is a hypothesis with one block of support, not a finding.

    #### What the cumulative estimate does to N = 150

    | | at `r` = 0.15 | at CI low 0.0710 | at CI high 0.2657 |
    |---|---:|---:|---:|
    | N for the floor (6) | 40 | 85 | 23 |
    | N for power 0.5 (15) | 100 | 212 | 57 |
    | **N for power 0.8 (23)** | **154** | 325 | 87 |

    **N = 150 now sits essentially exactly on the power-0.8 boundary**: 150 × 0.15 = **22.5 expected discordant pairs against the 23 required**. Half a pair short, well inside the estimate's own noise, and the floor is still cleared across the whole 95 % interval (85 at the pessimistic end).

    **The cheap option, stated because it is cheap: N = 160 restores the margin** — 160 × 0.15 = 24 ≥ 23 — for ten more questions, roughly 25–40 minutes. That is an owner decision and is not taken here. **The pre-registered position stands unless the owner moves it**: N = 150 was approved, and amendment 4 already accepts an inconclusive result on a representative set as an outcome rather than a failure.

    #### Deviation record (AC-14): this is the second interim look

    **Amendment 5 pre-committed one interim look, at 30.** This is look two, at 60, **owner-asked**. Recorded here rather than absorbed silently:

    - **The statistical argument is untouched.** `r` is a nuisance parameter at every look, not only the first; the blinding is unchanged, `b` and `c` were never computed, and pooling is done from the artifacts, which contain per-case discordance and nothing else — there is no split on disk to pool.
    - **What is actually spent is the thing the one-look rule was reserving:** each look is another opportunity to negotiate with the set. Amendment 5 said exactly this when it set the limit, so the cost is the one that was anticipated, not a new one.
    - **Pooling is only valid across a constant corpus**, and the artifacts carry `corpus_chunks` so the tooling can say whether it was. Both blocks: 358.

    **The split remains sealed.** Nine pairs disagreed across 60 questions. Which arm won each is not recorded anywhere in this repository.

    ---

    #### Block 3, and the first test of the topical-title hypothesis — measured 2026-08-04, artifact `evals/interim-block-3.json`

    30 questions, composition 21 / 5 / 4 as committed, document share 17 / 10 / 3, same authoring rule and quality bar, retained.

    **The prefix guard fired a second time, on its second use.** `con-082`'s gold was written `EU AI Act › Annex Xi`, which prefix-matches **`Annex Xii — ANNEX XII – Annex Xiii — ANNEX XIII`**. Caught before any embedding was spent, fixed to `EU AI Act › Annex Xi — ANNEX XI`. **Two instances in ninety questions is a rate, not an accident** — the Roman-numeral annex labels are a trap this corpus sets repeatedly, and a rule that catches them mechanically is worth more than the care of whoever writes the next block.

    | | Block 1 | Block 2 | **Block 3** | Cumulative |
    |---|---:|---:|---:|---:|
    | `n_discordant` | 7 | 2 | **7** | **16 / 90** |
    | `r` | 0.2333 | 0.0667 | **0.2333** | **0.1778** |
    | Silent full-text branch | 0/30 | 0/30 | **0/30** | 0/90 |
    | vector-only `recall@8` | 0.9000 | 0.9667 | **0.9667** | — |
    | vector-only **`MRR@8`** (banded) | 0.6511 | 0.7434 | **0.5879** | — |

    #### The topical-title hypothesis is not supported, and the proxy is why that is visible

    Block 2's read was that the block drew on **topically-titled sections**, making both arms' job easier, lifting `MRR@8` and suppressing discordance together. **The prediction was that a block 3 agreeing with block 2 would turn that from noise into drift.**

    **Block 3 went the other way on both numbers simultaneously**: `MRR@8` fell to 0.5879 — **below block 1**, −0.0632 from the reference — and `n_discordant` returned to 7. The two moved together again, in the opposite direction. So:

    - **Across three blocks the proxy and `r` co-move consistently** (0.6511/7, 0.7434/2, 0.5879/7), which is evidence that the proxy measures something real about how hard a block is.
    - **There is no monotone trend in either**, which is what drift would look like. Block 2 was the outlier and Fisher's exact already put it at p = 0.146.
    - **The band did not fire, and by amendment 6's terms it should not have.** Reported as the hypothesis failing its first test, not as a surprise.

    #### Where the set stands against the stopping rule

    | | Value |
    |---|---:|
    | Discordant pairs so far | **16 of 23** |
    | Pairs still needed | **7** |
    | Questions expected at `r` = 0.1778 | **≈ 40** |
    | Questions remaining to the cap | **110** |
    | Verdict | **CONTINUE** |

    At the current estimate the rule fires around **N ≈ 130**, i.e. during block 5 and therefore **at the block-5 boundary, N = 150** — which is where the superseded fixed-N plan would have stopped anyway, now reached by a rule that delivers the count instead of its expectation.

    #### Two defects in the tooling, both found in flight and both recorded

    **1. `cumulative` excluded the block that produced it.** It read artifacts from disk, and the current block's artifact is written *after* it runs — so the first block-3 run reported a pooled total over two blocks and called it three. **Wrong by exactly the newest data, and it produced a plausible smaller number rather than an error**, which is the same shape as the Annex bug one level up. The current block is now passed in rather than read back, and an artifact-level test asserts the pooled total equals the sum of the committed blocks.

    **2. One measurement discrepancy that could not be reproduced, recorded rather than explained away.** Block 2's `MRR@8` printed 0.7423 on one run and 0.7434 on the next, with no scoring change between them — a difference of exactly `1/5 − 1/6` over 30 questions, i.e. **one question's gold chunk moving between rank 6 and rank 5**. Five subsequent runs (two on block 2, three on block 3) agree to the digit, and embeddings were checked and are **bit-identical** for the same input.

    - **`n_discordant` — the quantity the stopping rule depends on — was stable across every run**, including the discrepant one.
    - **Candidate mechanism, offered as a hypothesis and not as a finding:** the planner choosing an exact scan over the HNSW index (or the reverse) between runs, which changes the approximate candidate set. Not pinned down, and **the honest statement is that this measurement has not been shown to be reproducible**, only observed to be reproducible six times out of seven. Under rule 7 that is a guarantee with neither a test nor a bound, so it is recorded as an open item rather than claimed.

    **The split remains sealed.** Sixteen pairs disagreed across 90 questions. Which arm won each is not recorded anywhere in this repository.

    ---

    #### Pilot-2 results — measured 2026-08-02, artifact `evals/pilot-2.json`

    14 questions, unchanged 358-chunk corpus, identical measurement path to pilot-1 (the same script, parameterised — a second copy would have been a second chance to differ from it).

    | | pilot-1 (hard, natural language) | **pilot-2 (hard, lexically anchored)** |
    |---|---:|---:|
    | Questions with **zero** full-text candidates | 13 of 14 | **0 of 14** |
    | Questions where hybrid's top-8 is identical to vector-only's | 14 of 14 | **6 of 14** |
    | `recall@8` hybrid | 0.714 | **1.000** |
    | `recall@8` vector-only | 0.714 | **0.857** |
    | Discordant pairs (b / c) | 0 / 0 | **2 / 0** |
    | `r` | 0.000 | **0.1429** |

    **Pre-registered outcome 1 obtains, and it is reported as such: the branch fires and discordance appears.** The full-text branch works for the case it was built for. **Pilot-1's 13-of-14 silence is a fact about question style, not a defect independent of style**, and SPEC-004 AC-12(b) is therefore a **scoping question, not a bug**. This is the less dramatic of the two answers and it is the one the measurement gives.

    **Scoping question does not mean small.** The mechanism is sound; its **operating envelope is far narrower than the architecture assumed**. Production queries arrive as sentences — that is what `/query` accepts and what SPEC-009 will send — and on sentences the branch is silent about half the time for citation-style questions and 93 % of the time for natural-language ones. The CLAUDE.md scoping note stands unchanged and must not be deleted by whichever option is chosen.

    **What made pilot-2's questions fire, recorded because it is the finding underneath the finding.** Three of eighteen drafts had to be rewritten even while deliberately using corpus vocabulary, and the words that killed them are instructive: `must` (the AI Act says *shall*), `long`, `mean`, `principal … identify`. **`shall` itself kills any NIST query, because NIST says *should*.** The corpus is three documents in three registers — EU legislative *shall*, NIST advisory *should*, SEC first-person *we* — and under AND semantics **a query cannot satisfy two registers at once**, so a cross-document question is close to guaranteed to return nothing.

    **The two discordant pairs are both `spans-two-sections`, both hybrid-only** (`anc-13`, `anc-14`: hybrid rank 1, vector-only absent from the top 8). Neither is evidence of anything on its own — `n_discordant = 2` is below the floor of 6, so `p = 0.5` and the comparison is **inconclusive**, exactly as the contract requires it to be reported.

    #### The recomputed N

    `r = 0.1429` → **`N = 6 / r = 42`** to reach the floor where rejection becomes *possible*. That is the number, and these three bounds travel with it:

    | | Value |
    |---|---|
    | Point estimate | **N = 42** |
    | 95 % Clopper-Pearson on `r` (2 of 14) | `[0.018, 0.428]` → **N ∈ [15, 338]** |
    | For power 0.50 at θ = 0.8 (needs 12 discordant) | **N ≈ 84** |
    | For power 0.80 at θ = 0.8 (needs 20 discordant) | **N ≈ 140** |

    **Three caveats that decide how much weight N = 42 can carry.**
    1. **42 buys the floor, not power.** At N = 42 the expected discordant count is exactly 6, where power is 0.26 at θ = 0.8. A set that can *possibly* reject is not a set that will.
    2. **`r` was measured on pilot-2's recipe, which the pre-registration fixed as a best case for the branch.** Vocabulary was constrained to what the corpus contains. Real queries will not be, so **0.1429 is an upper estimate and N = 42 is correspondingly a lower bound** for any realistic query mix.
    3. **The interval is enormous** — `N ∈ [15, 338]` from two discordant pairs. Reporting 42 without it would repeat the original sin of KD-12 at one tenth the size.

    **prereg-2's `N = 120` is now inside the interval and close to the power-0.8 figure of 140**, which is a coincidence worth naming rather than a vindication: 120 was `6/0.05` with the 0.05 assumed, and it lands near a defensible number for a reason unrelated to how it was derived.

    #### Both pilots re-run against the fixed branch — 2026-08-02

    Artifacts `evals/pilot-1-post-fix.json`, `evals/pilot-2-post-fix.json`. Same corpus, same questions, same measurement path; the only change is SPEC-004 AC-12 amendment 5.

    | | pilot-1 pre | pilot-1 **post** | pilot-2 pre | pilot-2 **post** |
    |---|---:|---:|---:|---:|
    | Zero full-text candidates | 13/14 | **0/14** | 0/14 | 0/14 |
    | Hybrid top-8 = vector-only's | 14/14 | **1/14** | 6/14 | 6/14 |
    | `recall@8` hybrid | 0.714 | **0.857** | 1.000 | 1.000 |
    | `recall@8` vector-only | 0.714 | 0.714 | 0.857 | 0.857 |
    | b / c | 0 / 0 | **3 / 1** | 2 / 0 | 2 / 0 |
    | `r` | 0.000 | **0.2857** | 0.1429 | 0.1429 |

    **Pilot-1 answers "do natural-language queries recover": yes.** Coverage went from 7 % to 100 %, hybrid gained 0.143 of `recall@8` over an unchanged vector-only arm, and discordance became measurable for the first time on that set.

    **Pilot-2 answers "did anything that worked stop working": no.** Every figure is identical before and after — which is the direct check that the fallback fires only where the conjunction was silent, and it is worth more than the improvement, because a fix that helps one shape at the cost of another would look like progress on pilot-1 alone.

    **`c = 1` appeared in pilot-1 for the first time.** One question where vector-only succeeds at k = 8 and hybrid does not. That cell was 0 across both pilots before the change, and it is the cell SPEC-004's pre-specified failure mode predicted would fill. **`n_discordant = 4` is still below the floor of 6, so the comparison remains inconclusive** and no reading of the b/c split is licensed.

    **The two `r` values disagree and that is the finding, not a nuisance: 0.2857 (pilot-1) against 0.1429 (pilot-2).** `r` is a property of the *question recipe*, exactly as amendment 2 pre-registered — it does not transfer between shapes, so a confirmatory set drawing on both would need its own measurement rather than either number. **No sizing is done here**, because prereg-2's `N` cannot be fixed while the mix of question shapes in the confirmatory set is undecided, and choosing the mix after seeing which one yields the larger `r` would be the substitution this key decision exists to prevent.

    #### Both pilots re-run again after frequency pruning — 2026-08-02

    Artifacts `evals/pilot-1-pruned.json`, `evals/pilot-2-pruned.json`.

    | | pilot-1 fallback | pilot-1 **pruned** | pilot-2 fallback | pilot-2 **pruned** |
    |---|---:|---:|---:|---:|
    | `recall@8` hybrid | 0.857 | 0.857 | 1.000 | 1.000 |
    | `recall@8` vector-only | 0.714 | 0.714 | 0.857 | 0.857 |
    | b / c | 3 / 1 | 3 / 1 | 2 / 0 | 2 / 0 |
    | `r` | 0.2857 | 0.2857 | 0.1429 | 0.1429 |

    **Both pilots are unchanged by pruning at k = 8.** Their questions are short and their lexemes are mostly discriminative, so few terms cross the 25 % threshold and the candidate sets barely move. The smoke set, whose questions are longer and carry more common vocabulary, is where pruning acts — helping at k = 1 and hurting at k = 3 and k = 8 (SPEC-004 AC-12 amendment 6).

    **`r` is unchanged on both sets: 0.2857 and 0.1429.** The two still disagree, still by recipe, and **no sizing is done** — for the same reason as before, and now with a second demonstration that `r` is a property of the question shape rather than of the system.

    **What the smoke set gained is one discordant pair in the `c` cell** (vector-only succeeds, hybrid fails) — the cell SPEC-004's failure mode predicted, now populated on a third set. Across smoke + pilot-1 + pilot-2 the totals are b = 5, c = 2, `n_discordant` = 7. **That is above the floor of 6 for the first time**, and it is **not** reported as a result: the three sets have different authoring recipes and pooling them would be exactly the shape-mix selection this key decision refuses to make after seeing the numbers.

    > **Recorded at the owner's instruction, because the refusal is the point** *(2026-08-04)*. Eleven rounds of work were aimed at clearing that floor — three of them at a corpus ladder that turned out to be the wrong lever entirely — and the first time it cleared, it cleared by pooling three sets with three different authoring recipes. **Declining to report it is the discipline this whole arc was building toward**, and it is written here rather than left implicit because the next person to assemble a number out of convenient parts will be doing it in good faith, in a hurry, and with a floor in sight.

    #### Pilot-1 results — measured 2026-08-02, artifact `evals/pilot-1.json`

    14 questions, unchanged 358-chunk corpus, nothing fetched, nothing ingested.

    | Set | n | recall@8 hybrid | recall@8 vector-only | discordant | `r` |
    |---|---:|---:|---:|---:|---:|
    | smoke (existing 26, article number in the question) | 26 | 1.000 | 1.000 | 0 | 0.00 |
    | **pilot (hard, no expected section in hand)** | 14 | **0.714** | **0.714** | **0** | **0.00** |

    **Result 1 — the binding constraint was the question set. `recall@8` de-saturated at 358 chunks, with no fetch.** 0.714 against 1.000, from changing nothing but how the questions were written. Four of fourteen missed entirely, and the misses are genuine rather than mislabelled: in three of the four the retriever returned the *predicted decoy* — `Article 49 — Registration` at rank 3 for the question whose answer is in Article 26(8), and `Item 1. Business › Competition` at rank 7 for the admission that lives in Risk Factors.

    **Result 2 — the sizing calculation does not produce a number, and "N is large" is the wrong reading.** `r = 0`, so `N = 6 / r` is **undefined**, not big. What the pilot supports is a one-sided 95% **upper bound** on `r` and therefore a **lower bound** on `N`: `r ≤ 0.193` → `N ≥ 32` from the pilot alone; pooled with the smoke set (0 discordant in 40) `r ≤ 0.072` → `N ≥ 84`. **There is no upper bound and no finite N.** The pooled figure mixes a deliberately hard set with a deliberately easy one and is a sanity check, not a rate for either population.

    **Result 3 — and this is the finding, because it explains the two zeros above by two different mechanisms.** The full-text branch returns **zero candidates** on 12 of 26 smoke questions and **13 of 14** pilot questions. Where it returns nothing, `hybrid` *is* `vector_only` — the pilot's top-8 was byte-identical on **14 of 14** questions. So:

    - **On the smoke set** the branch does fire (14 of 26) and does reorder the top 8 (13 of 26), but `recall@8` is saturated, so reordering inside a top-8 that already contains the gold cannot create a discordant pair.
    - **On the pilot set** `recall@8` is de-saturated, but the branch is silent, so the two arms are the same list and a discordant pair is *structurally impossible*.

    **Neither set can produce a discordant pair, and neither reason is about corpus size.** No rung would have fixed either. This is why the pilot came before the fetch.

    **What this does to prereg-2.** `assumed_discordance: 0.05` is falsified — measured 0 with an upper bound of 0.193 on the pilot recipe. `N = 120` is **not** supported and **not** refuted; it now sits inside `[32, ∞)`. **Authoring 94 more questions of either existing style would buy nothing**, because both styles are on a branch of the vise. The confirmatory set cannot be sized until Result 3 has an owner decision, and that is recorded here rather than worked around.

    **The exclusion is not a formality.** A pilot that measures the rate and then
    contributes its cases to the analysis it sized is the same substitution
    amendment 1 exists to prevent, wearing a new name: the cases that set the
    threshold would be among the cases judged against it, and they were selected
    for being hard. Pilot ids are recorded in the artifact and the confirmatory
    set asserts it shares none of them.

    **Consequence, stated so it is not discovered later: `baseline-358-chunks.json`
    is a prereg-1 artifact and is not comparable to any prereg-2 rung.** Rung 0
    must be re-measured against the 120-question set before Rung 1's measurement
    means anything. That re-measurement is cheap (~120 embeddings, well under a
    cent) and it does **not** require re-ingesting the corpus — but it does
    require the 120 questions to exist, which makes authoring them a prerequisite
    of the ladder rather than a follow-up to it.

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

- **AC-17 (the interim sizing look cannot see which arm won — Key decision 12 amendment 5)** *(added 2026-08-04)* — `scripts/interim_r.py` reduces paired outcomes to `n`, `n_discordant`, `r` and the shape composition, and:
  - **The discordance summary is invariant under swapping the arms.** For any input, `summarise(rows) == summarise(mirror(rows))`, asserted on a maximally lopsided case (9 hybrid-only vs 1 vector-only, mirrored to 1 vs 9) so that a leak has something to leak. A summariser that reveals direction through *any* channel — a field, a rounding, an ordering — fails this, which is why it is stated as invariance rather than as a list of forbidden fields.
  - **The published artifact does not determine the split** *(amended 2026-08-04, amendment 6)*. Arm-swap invariance covers `summarise` and **cannot** cover the artifact once it carries a single-arm difficulty proxy, since a single-arm quantity is not invariant under swapping the arms by construction. The artifact-level guarantee is the provable one: `n`, `n_discordant = b + c` and `vector_hits = both + c` are three equations in four unknowns, so `c` is free across its feasible range. Asserted on the instance `(b, c) = (7, 0)` versus `(4, 3)` — same `n`, same discordant case ids, same vector hits, byte-identical output — with a paired sensitivity check showing that adding the hybrid arm's recall separates them at once.
  - **Top-level and per-case keys match an allowlist**, not a denylist: a key the allowlist does not name fails the test whether or not anyone judges it to encode direction. Blinding fails closed or it does not hold.
  - **Per-case records carry no rank and no per-arm hit** — only `id`, `shape`, and `discordant` — so the split cannot be reconstructed from the artifact after the fact.
  - **Verified by mutation:** adding `hybrid_only` to the summary fails the invariance test; adding a per-case `hybrid_rank` fails the allowlist test; computing discordance as `b + c` from two directional counts rather than as `hit_a != hit_b` fails neither on its own, which is why the invariance test is the load-bearing one and the allowlist is the backstop.
  - **Bound:** this covers the reporting path, not the data. Anyone can re-measure the split deliberately from the public repository in about a minute; what is foreclosed is seeing it as a side effect of asking for the interim number.

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
