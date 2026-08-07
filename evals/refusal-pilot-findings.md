# Near-miss refusal pilot — findings

**Run 2026-08-07** against `evals/prereg-refusal-nearmiss.md`, registered the
same day and before any question was authored. 30 questions, $0.531006,
`anthropic:claude-sonnet-5`, prompt `v1`, k = 8, 358 chunks unchanged.

**Status: pilot.** Not a figure and not renderable as one. It decides whether an
expensive step happens; it settles nothing by itself.

---

## 1. The primary metric, in the form it was committed in

Refusal rate = the fraction of questions whose **verdict is
`INSUFFICIENT_EVIDENCE`**.

| arm | refused | rate | Wilson 95% |
|---|---:|---:|---|
| **A — near-miss unanswerable** | 1 / 10 | 0.100 | [0.018, 0.404] |
| **B — absent-topic unanswerable** | 6 / 10 | 0.600 | [0.313, 0.832] |
| **C — answerable (positive control)** | 0 / 10 | 0.000 | [0.000, 0.278] |

**A − B = −0.500**, 95% interval **[−0.746, −0.082]**, unpaired square-and-add of
the two Wilson intervals — as pre-registered in §5, because the arms are matched
by question *form* and not by item, so there is no per-item correlation to
estimate and using the paired construction would be SPEC-007 KD-13 amendment 1's
defect with the sign reversed.

### Outcome: **indeterminate**

Against §6's committed thresholds: outcome 1 needs A ≥ 8 **and** B ≥ 8 — A is 1.
Outcome 2 needs B ≥ 8 and A ≤ 5 — B is 6. Outcome 3 needs B ≤ 5 — B is 6.

**B = 6 lands in the fourth cell**, which was named in advance precisely so this
result could not be filed under whichever of the three it most resembles. It is
one question away from outcome 3 in one direction and two from outcome 2 in the
other. It is not rounded here.

---

## 2. Why the primary metric is not the finding

**13 of the 20 unanswerable questions carry `verdict: answered` on a body that
declines.** Not partially, not ambiguously — several decline by writing the
literal string `INSUFFICIENT_EVIDENCE` in the answer body, one line after the
parser has already read `ANSWERED` off line 1 and stripped it.

Two of them stop and correct themselves mid-answer:

> A2a — *"Correction: Since the excerpts do not actually provide the number of
> employees in China specifically, the correct verdict is:
> **INSUFFICIENT_EVIDENCE**"*

> A5a — *"Wait, let me correct this properly. **INSUFFICIENT_EVIDENCE** — None of
> the provided excerpts come from Annex XIV of the EU AI Act"*

**The mechanism is deducible from the parser rather than guessed.**
`AnswerParser._feed_verdict` maps the first line through `_VERDICT_TOKENS` and
defaults to `ERROR`; **zero records came back `ERROR`**, so every first line was
a valid token, and every one of these 13 was literally `ANSWERED`. The model is
not failing to emit a verdict. **It is committing to `ANSWERED` on the first
token and then writing the honest refusal underneath.**

This is the failure SPEC-005 Key decision 7 anticipated — *"a first-line verdict
with no prior reasoning creates confabulation pressure"* — arriving in a form the
decision did not predict. The pressure did **not** produce confabulated content.
It produced **verdict/content divergence**, which is worse in one specific way:
the content is right, so nothing downstream that reads the prose looks wrong,
and everything downstream that reads the field is wrong silently.

---

## 3. The content adjudication (post-hoc — see deviation 2)

Every unanswerable answer read against its cited chunks, with the declining
clause quoted in `evals/refusal-pilot-result.json`:

| arm | declined on content | rate | Wilson 95% |
|---|---:|---:|---|
| A — near-miss | 10 / 10 | 1.000 | [0.723, 1.000] |
| B — absent-topic | 10 / 10 | 1.000 | [0.723, 1.000] |
| C — answerable | 0 / 10 | 0.000 | [0.000, 0.278] |

**Confabulations: 0 of 20. Questions disqualified under §3.2: 0** — no arm-A
answer turned out to be present in the corpus after all.

**Arm C answered all ten, and all ten are correct** — CE marking, the four AI RMF
Core functions, EUR 35 000 000 / 7 %, 15 days for a serious incident, ~42,000
employees, Compute & Networking and Graphics, fabless manufacturing, the Act's
inference-based definition, the RMF's "AI actors" audience, and Article 14
human-oversight competence. **Rule 9's check passes: the pipeline is not
refusal-biased**, so a high decline rate on A and B is readable as
discrimination rather than as a model that declines a lot.

**By these numbers the answer is outcome 1** — the corpus is adequate for
refusal, and the concern was about question construction, as it was for
retrieval. **That reading rests on a metric invented after the data was seen,
and it is reported as such.** The pre-registered outcome is indeterminate.

---

## 4. What this does and does not settle

**Settles, on the retrieval-and-refusal question the pilot was built for:** the
premise behind fetching documents is not supported. Ten near-miss unanswerables
were authored **against the unchanged 358-chunk corpus**, on topics the corpus
covers squarely — retrieval put the right Article, the right Item, the right RMF
function at rank 1 for all ten — and the specific answer genuinely absent. The
generator declined all ten. **A three-document corpus was not the binding
constraint on constructing hard unanswerables, and it was not the binding
constraint on answering them correctly.**

**A4a is the case worth reading**, because it is the strongest form of the shape:
its **rank-1 chunk is the 10-K's Item 1C board-level cybersecurity governance
disclosure** — the corpus holds the answer, under the wrong instrument — and the
model declined *and* said where the obligation actually lives.

**Does not settle: groundedness.** KD-7 amendment 1's premise has two conjuncts
and this pilot measured one. *The corpus leaves the generator few
plausible-but-wrong chunks to be wrong with* is untouched by a result in which
the generator was never wrong. Zero confabulations across 20 questions is
consistent both with "the corpus is fine" and with "these questions could not
produce drift"; separating them needs a groundedness falsifier, not this one.

**Does not settle: the sizes.** n = 10 per arm. Every interval above is roughly
±0.28 wide at the middle. The 1.000s carry a lower bound of 0.723.

**Not a claim about production refusal.** Twenty unanswerable questions written
by one author against three documents is not a refusal rate. It is evidence that
this corpus can carry near-miss questions at all, which is what was in doubt.

---

## 5. The consequence that is not about the corpus

**`verdict` is unreliable on unanswerable questions in the shipped system.**
13 of 20 declining answers were tagged `answered`. This is not an eval artifact:
it is SPEC-005's contract, it is the field SPEC-006 returns to clients, and
SPEC-009 is planned to render a refusal differently from an answer. On this
evidence it would render 13 of 20 correct refusals as answers.

**Recorded as a finding and a Proposed amendment against SPEC-005, not applied**
(CLAUDE.md rule 4): nobody asked for it, and the choice between the available
fixes is a design decision.
