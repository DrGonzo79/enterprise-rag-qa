# Near-miss refusal pilot, run 2 — findings

**Run 2026-08-07** against `evals/prereg-refusal-nearmiss.md`, unchanged. Same
30 questions, same three arms, same thresholds, same corpus (358 chunks, not
touched). **Prompt v2**, with the verdict field fixed under SPEC-005 Key
decision 7 amendment 1. $0.559266.

**Supersedes run 1, which was void** — not because its answers were wrong, but
because its instrument was.

**Status: pilot.** Not a figure. It decides whether an expensive step happens.

---

## 1. The primary metric, in the form it was committed in

Refusal rate = the fraction of questions whose **verdict is
`INSUFFICIENT_EVIDENCE`**.

| arm | refused | rate | Wilson 95% |
|---|---:|---:|---|
| **A — near-miss unanswerable** | 9 / 10 | 0.900 | [0.596, 0.982] |
| **B — absent-topic unanswerable** | 10 / 10 | 1.000 | [0.723, 1.000] |
| **C — answerable (positive control)** | 0 / 10 | 0.000 | [0.000, 0.278] |

**A − B = −0.100**, 95% interval **[−0.404, 0.189]**, unpaired square-and-add of
the two Wilson intervals, as pre-registered in §5. **The interval spans zero:**
near-miss and absent-topic refusal are not distinguishable at this n.

### Outcome: **1 — refuses both**

Committed thresholds: outcome 1 requires **A ≥ 8 and B ≥ 8**. A = 9, B = 10.

> **The corpus is adequate for refusal, and the concern was about question
> construction — as it was for retrieval.**

**Arm C's rule-9 check passes.** 0/10 refused against a pre-registered ceiling of
2, and all ten answers are correct. Outcome 1 is the reading that a
refusal-biased pipeline would also produce, and arm C is the only thing that
separates them. It separates them.

---

## 2. What the fix did to the instrument — reported separately, on purpose

The same 30 answers, scored on **run 1's field** (the header token alone):

| arm | header-only | primary (reconciled) |
|---|---:|---:|
| A — near-miss | 5 / 10 | 9 / 10 |
| B — absent-topic | 6 / 10 | 10 / 10 |
| C — answerable | 0 / 10 | 0 / 10 |

**Header-only outcome: indeterminate** — A = 5, B = 6, the fourth cell again.
**The fix changed the outcome of the experiment.** That is worth stating
plainly, and it is also the reason it is in its own section: a fix chosen
because it moves a number is a fix chosen by the number, and this one was
chosen on correctness before either run 2 or this table existed (SPEC-005 KD-7
amendment 1's four-option table, decided with the metric effect quarantined).

**Reconciliation fired on 8 of 30**, and **every single one was
`answered → insufficient_evidence`.** None went the other way. The header is not
noisy — it is biased, in exactly the direction Key decision 7 predicted the
pressure would point.

**Residual divergence: 1 of 20.** A1b declines in prose — *"No numeric
percentage reduction is given in the provided excerpts"* — under
`verdict: answered`, with **both tokens agreeing**, so reconciliation had
nothing to override. It is arguably a correct `ANSWERED`: it states what Article
62 does say (fees reduced proportionately to size and market size) while noting
that no percentage exists. **Recorded as ambiguous rather than resolved.** The
primary is the verdict token and it reads 9/10 either way.

**13 of 20 → 1 of 20**, and the one that remains is a case where the label is
genuinely arguable rather than a case where the field is wrong.

---

## 3. What this settles

**The premise behind fetching documents is not supported.** Ten near-miss
unanswerables were authored against the **unchanged 358-chunk corpus**, on
topics the corpus covers squarely — retrieval put the right Article, Item or RMF
function at rank 1 for all ten — with the specific answer genuinely absent. The
generator declined nine of ten by the pre-registered metric, and the tenth is the
ambiguous one.

**A three-document corpus was not the binding constraint on constructing hard
unanswerables, and was not the binding constraint on declining them.** This is
the third time the cheap falsifier has beaten the expensive step in this project
— fourteen rewritten questions settled retrieval saturation, pilot-2 found the
lexical branch fired at all, and thirty questions and a dollar have now answered
the refusal half of the corpus argument.

**A − B spans zero, which is its own small finding.** The near-miss arm was
constructed to be *harder* than the absent-topic arm, and at n = 10 per arm the
data cannot show it is. The pilot's original worry — that absent-topic questions
are unrealistically easy — is not visible in the refusal rate, because **both are
declined**. That is outcome 1 and not a failure to detect a difference: there is
no gap to size.

---

## 4. What this does not settle

**Groundedness. The gate is half-answered, and the answered half was the cheap
half.** The premise has two conjuncts and this measured one:

- *unanswerable questions are unrealistically easy to construct* — **answered**;
- *the corpus leaves the generator few plausible-but-wrong chunks to be wrong
  with* — **untested, and untestable by this experiment.** The generator was
  never wrong across 20 unanswerable questions, and **a result in which nothing
  drifted cannot measure drift.** Zero confabulations is equally consistent with
  "the corpus is fine" and "these questions could not have produced drift".

A groundedness falsifier needs questions where a wrong answer is *available* to
be given — answerable questions whose corpus support is partial, adjacent, or
spread across documents that use similar language. That is a different set and a
different pre-registration.

**The sizes.** n = 10 per arm; every interval is roughly ±0.28 wide at the
middle, and the 1.000 carries a lower bound of 0.723.

**Not a claim about production refusal.** Twenty unanswerable questions written
by one author against three documents is not a refusal rate. It is evidence that
this corpus can carry near-miss questions at all, which is what was in doubt.

**Not a claim that the verdict field is now correct in general.** It is correct
on these 30, under this prompt, with this model. What the amendment actually
bought is that the disagreement is now **a number in a column** rather than
something nobody could have seen.
