# SPEC-007 — Evaluation harness

**Status:** Draft — awaiting review by the repository owner

**Date:** 2026-08-02
**Depends on:** SPEC-002 (`eval_runs` / `eval_results`), SPEC-003 (corpus, de-saturation gate), SPEC-004 (retrieval, tuning metric), SPEC-005 (generation)
**Consumed by:** SPEC-009 (the explanatory panel renders this spec's report)

**Your brief was cut off mid-sentence, so this draft is built on part of it.**
The text received ended at: *"What the eval measures and what a number in it
licenses someone to claim. If a figure will appear in the degraded"* — the
sentence stops there, and any bullets after it did not arrive. Key decision 1
below is written to the complete bullet plus the evident continuation (a figure
rendered in SPEC-009's explanatory panel is a claim made to strangers, so it
needs a stated warrant). **Everything else here is derived from what the
repository already binds** — SPEC-004 Key decision 12a, SPEC-004 AC-6a, SPEC-003's
cross-spec note on golden-set authoring, and the RRF paraphrase regression
measured 2026-07-26 — and is flagged as such. Please supply the rest of the
brief; the missing bullets may well change the shape of the Interface section.

**One prerequisite is binding and currently unmet.** SPEC-004 Key decision 12a:
*do not report retrieval metrics, tune fusion, or set quality floors against a
corpus that has not passed SPEC-003 AC-10 (de-saturation)*. At 358 chunks
recall@3 and recall@8 are pinned at 1.000 for both methods, which makes this
harness **unfalsifiable as scoped**. Corpus expansion is a prerequisite, not an
enhancement. See Key decision 6.

## Purpose

Make the project's central claim checkable by someone who does not trust it.

Everything else in this repository produces an answer; this produces a number
about how good those answers are, and the number is the thesis. That places an
unusual obligation on it: **a figure this harness publishes will be read by
people who cannot inspect how it was produced**, because SPEC-006 Key decision 16
binds SPEC-009 to render the eval report in the explanatory panel — to visitors,
on the demo's worst day. A metric that overstates what it knows is worse than no
metric, because it converts an honest project into a misleading one at exactly
the moment it is being evaluated.

Three things, in order:

1. **Score answers against a golden set**, including refusals, and record every
   run in `eval_runs` / `eval_results` so a result is attributable to a corpus
   state, a commit, and a configuration.
2. **Adjudicate open retrieval questions** that the repository has deliberately
   left open — first among them whether Reciprocal Rank Fusion beats vector-only,
   which was measured on 2026-07-26 as a *loss* on paraphrase questions and has
   been held un-tuned since, waiting for this harness.
3. **Publish a report whose every figure carries its warrant** — sample size,
   corpus state, and what it does not say.

## Non-goals

- **No LLM-as-judge in v1.** It replaces a measurement problem with a second,
  unmeasured model. Revisit only with a human-labelled agreement study, which is
  itself a research task this project has no room for.
- **No leaderboard, no public benchmark comparison.** This corpus and question
  set are ours; a number from them is not comparable to anyone else's and
  presenting it as if it were would be the overstatement this spec exists to
  prevent.
- **No tuning inside the harness.** The harness measures; a person changes the
  system and re-measures. A harness that also searches the configuration space
  optimises against its own test set by construction.
- **No online / per-request evaluation.** SPEC-006 Non-goals already put this
  offline against the libraries, not over HTTP.
- **No regression gate in CI in v1.** See Key decision 7 — it needs a floor, and
  a floor cannot be set before the corpus is de-saturated.

## Interface

```
evals/
  golden/            # the scored set: question, expectation, provenance
    answerable.jsonl
    unanswerable.jsonl
  retrieval/         # SPEC-004 AC-6a's separate, larger, retrieval-only set
    cases.jsonl
  reports/
    report-<git-sha>-<chunks>-chunks.json     # immutable, one per run
    latest.json                               # convenience copy
```

```bash
uv run python -m rag_qa.evals.run --set golden --config default   # costs money
uv run python -m rag_qa.evals.run --set retrieval                 # no generation
uv run python -m rag_qa.evals.report <run-id>                     # render only
```

**Two sets, and they are not interchangeable** (SPEC-004 AC-6a, binding):

| | golden | retrieval |
|---|---|---|
| Size | 50 (predates any power analysis — see KD-5) | as large as authoring allows |
| Calls the model | yes — costs real money | **no** |
| Measures | end-to-end verdict + citation correctness + refusal | recall@k, MRR@k |
| Runs | deliberately, by a person | freely, in CI once de-saturated |

**A case:**

```jsonc
{
  "case_id": "eu-ai-act-art6-2",
  "question": "What does Article 6(2) classify as high-risk?",
  "kind": "answerable",              // or "unanswerable"
  "expected_sections": ["EU AI Act › CHAPTER III › Article 6"],
  "unanswerable_verified_at": null,  // required when kind == "unanswerable"
  "notes": "authored from Annex III; competition from the 10-K risk factors"
}
```

**A report figure** — every published number is this shape, never a bare scalar:

```jsonc
{
  "metric": "refusal_rate_on_unanswerable",
  "value": 0.86,
  "n": 22,                            // the denominator, always
  "interval": [0.65, 0.97],           // Wilson, 95%
  "corpus_chunks": 1180,
  "git_sha": "…",
  "decided": 22,                      // cases where the metric could discriminate
  "claim": "On 22 questions verified unanswerable against this corpus, the system declined 19.",
  "not_a_claim": "That it declines 86% of unanswerable questions in general."
}
```

## Key decisions

1. **Every published figure carries its warrant, and `claim` / `not_a_claim` are
   required fields rather than documentation.** This is the decision the rest of
   the spec serves. A number in the explanatory panel is read by someone who
   cannot see the harness, and the failure mode is not fabrication — it is a true
   number licensing a false inference. **The repository already has the worked
   example:** SPEC-004 AC-6 asserted hybrid recall@1 > vector-only, measured
   0.929 vs 0.857, and that margin was *one net question out of fourteen*, from a
   2–1 split of three decided questions. It was written up as evidence that
   hybrid retrieval works. It was a coin flip, and SPEC-004 Key decision 12 had
   to be amended to withdraw the claim while keeping the assertion. **A figure
   without `n`, `decided`, and an interval would have made that correction
   impossible to notice**, because the number itself was never wrong. So: no
   scalar is published alone, `n` is the denominator not the corpus size,
   `decided` is the count of cases where the metric could have come out either
   way, and `not_a_claim` states the generalisation the figure does **not**
   support.

2. **Refusal is scored as a capability, symmetric with answering.** CLAUDE.md
   makes declining a tested capability and SPEC-006 Key decision 1 makes it a
   200. So the harness reports a **2×2**, not an accuracy: answered-correctly,
   answered-when-it-should-have-declined (the expensive error), declined-correctly,
   declined-when-it-could-have-answered. A single "accuracy" figure would let a
   system that never refuses and a system that always refuses land on the same
   number, and the whole refusal design would become invisible to the metric
   meant to justify it.

3. **Unanswerability is a claim about the corpus and is verified by retrieval,
   not asserted by the author** (SPEC-003's cross-spec note, binding). With
   overlapping regulatory material, a question authored as unanswerable from one
   document may be answerable from another the author did not have in mind.
   `unanswerable_verified_at` records the corpus state the verification ran
   against, and **a case whose verification predates the current corpus is not
   scored** — it is reported as stale, loudly, because silently scoring it turns a
   corpus expansion into a fake refusal failure or, worse, a fake success.

4. **A run is attributable or it is not a run.** `eval_runs` already carries
   `git_sha`, `dataset_name`, `config`, `created_at`; the report adds the corpus
   chunk count. Any figure that cannot name all four is not published. This is
   what makes a before/after across corpus states a finding rather than two
   numbers, and it is the same argument SPEC-003 AC-13 made for immutable
   baselines — which this spec extends to reports.

5. **50 golden questions is a number that predates any power analysis, and it is
   not defended here.** SPEC-004 Key decision 12a says so explicitly: power comes
   from the retrieval-only set, not from the golden set and not from more
   documents. So the golden set is sized for *coverage of the failure modes*
   (multi-source, citation-exact, paraphrase, unanswerable, near-miss) and the
   report must not quote a golden-set difference as significant. **Flagged as the
   decision most likely to be wrong**: if the intended use is comparing two
   configurations end to end, 50 is too few and the honest fix is a power
   calculation before authoring more, not after.

6. **The corpus must pass SPEC-003 AC-10 before this harness reports anything —
   inherited, not chosen here.** SPEC-004 Key decision 12a is binding: at 358
   chunks recall@3 and recall@8 are 1.000 for both methods, so the metrics carry
   no information, and only k=1 discriminates. Reporting against a saturated
   corpus produces figures that are true, stable, and meaningless — the exact
   thing Key decision 1 exists to prevent, arriving through the corpus rather
   than through the arithmetic. **Consequence for sequencing:** this spec can be
   written and its harness built, but its first published report waits on
   de-saturation.

7. **No CI regression gate in v1, and the reason is that a floor cannot be
   honestly set yet.** The obvious design fails a build when a metric drops.
   SPEC-004 AC-6 deliberately declined to set an absolute floor, on the grounds
   that "a provisional number amended to match the first run would be a
   measurement wearing a standard's clothes" — and that reasoning holds here with
   more force, because a gate makes the number load-bearing for merges. **Revisit
   when** the corpus is de-saturated and two consecutive runs at the same corpus
   state have established run-to-run variance; the floor is then set below
   observed variance, not at the last run's value.

8. **The RRF-vs-vector-only question is this harness's first job, and it is
   currently open with a measured regression against it.** Measured 2026-07-26:
   plain RRF loses to vector-only overall on paraphrase questions. Nothing has
   been tuned since, deliberately, because tuning before a harness exists means
   tuning against a number nobody can reproduce. The retrieval set adjudicates
   it, and **the answer is allowed to be "vector-only wins"** — CLAUDE.md's
   hybrid-retrieval rationale is a hypothesis this measures, not a commitment it
   defends. If hybrid loses on a de-saturated corpus, the finding is published
   and the stack decision is revisited by amendment.

9. **A golden run costs real money and is therefore explicit, budgeted, and
   recorded.** 50 questions × generation is a live provider spend, and SPEC-006's
   ceiling does not apply — this runs offline against the libraries, not through
   the API. So the runner prints the estimated cost and requires a flag to spend,
   the same shape SPEC-006 Key decision 12 chose for `/ingest` (dry-run by
   default, because the destructive expensive call should be the one you ask
   for), and the actual cost is a field on the report.

## Acceptance criteria

- **AC-1 (no bare scalar is publishable)** — Every figure in a report carries
  `n`, `decided`, an interval, `corpus_chunks`, `git_sha`, `claim`, and
  `not_a_claim`; a report containing a figure missing any of them fails to
  render, asserted by constructing one. The renderer has no code path that emits
  a value without its warrant — asserted structurally, not by inspecting output,
  since the failure is an omission.
- **AC-2 (the warrant is not decorative)** — Reconstructing SPEC-004's 2026-07-26
  measurement as a report (recall@1 0.929 vs 0.857, three decided questions)
  produces `decided: 3` and an interval spanning the difference, and its
  `not_a_claim` states that it is not evidence hybrid retrieval works. The
  historical over-claim is the fixture: if this harness would have published that
  result as evidence, the field set is wrong.
- **AC-3 (refusal is scored as a 2×2)** — A run reports all four cells. A system
  that never refuses and one that always refuses produce visibly different
  reports, asserted with two stub generators, since a metric that cannot separate
  them is the metric this criterion exists to reject.
- **AC-4 (a stale unanswerable case is not scored)** — A case whose
  `unanswerable_verified_at` predates the current corpus state is excluded and
  reported as stale; asserted by moving the corpus state forward and re-running.
  Scoring it either way — as a pass or a fail — is asserted **not** to happen.
- **AC-5 (a run is attributable)** — Every `eval_results` row joins to an
  `eval_runs` row carrying `git_sha`, `dataset_name`, `config`, and the corpus
  chunk count; a run started with a dirty working tree records that fact.
- **AC-6 (reports are immutable, like baselines)** — A second run at the same
  git sha and corpus state does not overwrite the first report; the write guard
  fails the run, reusing SPEC-004 AC-13's mechanism rather than a second one.
- **AC-7 (the retrieval set runs without spending)** — `--set retrieval` makes
  **zero** provider generation calls, asserted by counting calls on a fake
  client, so it can run freely once the corpus is de-saturated.
- **AC-8 (a golden run will not spend without being asked)** — The default is an
  estimate and no spend; spending requires an explicit flag; the report records
  the actual cost. Asserted by running the default and observing zero calls.
- **AC-9 (the de-saturation prerequisite is enforced, not documented)** — Running
  a report against a corpus that has not passed SPEC-003 AC-10 fails with a named
  cause rather than producing figures. A binding constraint that only exists in
  prose is one someone follows until they are in a hurry.

## Test plan

`tests/test_evals_*.py`. The harness's own tests use **stub generators and the
seeded synthetic corpus**, never the real corpus and never a provider — a test
suite that spends money to run is a test suite that stops being run.

**The shape to be most suspicious of here is a report test that asserts on the
report object rather than on the rendered report** (CLAUDE.md rule 3's closing
sentence, and the sixth and seventh instances in its list). AC-1's structural
assertion is deliberately about the absence of a code path, because "no figure
lacks a warrant" cannot be proved by checking figures that happen to have one.

**Every acceptance criterion is verified by breaking the behaviour it covers.**
The mutations that matter: emit a figure without `n`; score a stale unanswerable
case instead of excluding it; let the retrieval set call the generator; let a
second report overwrite the first; remove the de-saturation check.

**AC-2's fixture is a historical result, on purpose.** It is the only case in
this repository where an over-claim actually happened and was caught, so it is
the only fixture that tests the guard against something other than the author's
imagination.
