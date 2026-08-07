# Pre-registration — near-miss refusal pilot

**Registered 2026-08-07, before any question in it was authored.** The commit
that adds this file adds nothing else; the questions arrive in a later commit.
That ordering is the whole point and it is checkable in `git log`.

**Status: pilot.** Not a figure, not renderable as one, and it settles nothing on
its own. It exists to decide whether an expensive step happens at all.

---

## 1. What this is falsifying, and why it runs before anything is fetched

SPEC-007 Key decision 7 amendment 1 scoped the de-saturation gate to retrieval
and left the generation side **open**, with a stated premise:

> **A three-document corpus makes unanswerable questions unrealistically easy to
> construct.** "What does the Act say about maritime cabotage?" is unanswerable
> here for a trivial reason — the topic is absent — rather than for the reason
> refusal is hard in production, which is a question the corpus *nearly* answers.

CLAUDE.md rule 8 says the premise behind an expensive step gets the cheapest
falsifier available, and it runs first. **The expensive step is fetching
documents** — the corpus ladder, unapproved since 2026-08-02, seven documents at
`probe-candidate`. **The cheap falsifier is authoring harder questions against
the corpus that already exists**, which is the same move that settled retrieval
saturation (fourteen rewritten questions, $0.00002, against a ladder that had
reached three review rounds and $0.0176 of probe spend).

**Third time asked, so the tell is worth restating**: the premise names a
variable that is hard to change (the corpus) and holds fixed one that is easy to
change (the questions). That ordering is what earns a paragraph of scrutiny.

**What this pilot does NOT test, stated because the premise has two conjuncts and
this covers one.** The second conjunct — *the corpus leaves the generator few
plausible-but-wrong chunks to be wrong with*, which is about **groundedness**,
not refusal — is not measured here. A confabulation result on arm A would be
evidence for it; a clean refusal result on arm A leaves it untouched, and
groundedness would need its own falsifier.

---

## 2. Arms and shapes — committed here, before authoring

Three arms. Two are the owner's; the third is required by rule 9 and the reason
is in §6.

### Arm A — near-miss unanswerable (n = 10)

**The topic is squarely in the corpus and the specific answer is not.** Two
questions in each of five shapes, fixed here:

| shape | form | example of the form (not a question in the set) |
|---|---|---|
| **A1 unstated threshold** | the document regulates the thing but sets no number, date or period for *this* aspect of it | a notification deadline the Act does not fix |
| **A2 undisclosed figure** | the 10-K discusses the item but does not break out the specific quantity | a cost line the filing aggregates rather than discloses |
| **A3 unnamed control** | the framework covers the function but names no specific control, tool or technique for it | a named testing method the RMF does not prescribe |
| **A4 wrong instrument** | the concept exists in the corpus but belongs to a *different* document than the one asked about | an Act obligation asked of the RMF |
| **A5 out-of-scope period or entity** | the document exists but does not cover the year, annex or entity named | a fiscal year outside the filing |

### Arm B — absent-topic unanswerable (n = 10), the control

**Matched to arm A by question form, one for one**, and by nothing else: for each
arm-A question there is an arm-B question of the same shape whose *topic* is
outside all three documents. Matching is by form only — these are not paired
observations on a common item, and §5 says what follows from that.

### Arm C — answerable (n = 10), the positive control

Ten questions the corpus **does** answer, in the same five shapes where the shape
admits an answerable form, drawn from the same documents in the same proportions.

### Document proportions, fixed here

Arms A and C draw **5 / 3 / 2** from the EU AI Act / NVIDIA 10-K / NIST AI RMF,
roughly the corpus's own 201 / 119 / 38 chunk split. Arm B follows arm A's
one-for-one match, so its "document" is the document its partner asks about.

---

## 3. Authoring rules, and what disqualifies a question

Authored by the same person who built the retriever. That is arm A's largest
threat and it cannot be fixed here — it is bounded instead:

1. **Every arm-A question is verified before it is run**, by retrieving at k = 8
   and reading the returned chunks, plus a targeted search of the source document
   for the specific quantity/date/name asked for. A question survives only if the
   answer is genuinely absent.
2. **A question is disqualified — and reported as disqualified, not quietly
   replaced — if**, after the run, its answer turns out to be present in the
   corpus. Disqualified questions are reported with the count and the text.
3. **Arm-A questions must not signal their own answer.** No "does the Act specify
   …", no "is there any …" — the grammar must be the grammar of an answerable
   question, because a question that announces it expects a refusal measures the
   phrasing, not the corpus.
4. **Arm B is authored from arm A's forms**, after arm A is fixed, and never the
   other way round.
5. **Question text is frozen before the first generation call.** No question is
   edited after any verdict is seen.

---

## 4. Run configuration

- Retrieval: the shipped dense path, **k = 8**, no filters, planner pinned as in
  `scripts/query_plan.py`.
- Generation: `anthropic:claude-sonnet-5`, `PROMPT_VERSION` as shipped, unedited
  `SYSTEM_PROMPT`. **The prompt is not tuned for this pilot**, before or after.
- `SpendSource.EVAL`, so this presses the monthly ceiling and not the daily
  visitor one.
- **Cost bound: $0.40.** 30 questions at ~3k input and ~250 output tokens each,
  at the $2 / $10 per Mtok rate in force until 2026-08-31, is ~$0.26; the bound
  is that with headroom. If the run exceeds it, it stops and reports.
- Everything is recorded: question, arm, shape, retrieved `section_path`s,
  verdict, answer text, citations, tokens, cost.

---

## 5. Metric and analysis

**Primary metric: refusal rate per arm** = the fraction of questions whose
verdict is `INSUFFICIENT_EVIDENCE`.

- `PROVIDER_REFUSED`, `TRUNCATED` and `ERROR` are **not** refusals and are
  reported separately. Folding a classifier refusal into an evidence refusal is
  the conflation SPEC-005 KD-5 made a separate verdict to prevent.
- Each arm's rate is reported with a **Wilson interval**, and at n = 10 those
  intervals are wide by roughly ±0.28 at the middle. That is not a defect to be
  apologised for later; it is the reason this is a pilot.
- The **A − B difference** is reported with the **unpaired** square-and-add of
  the two Wilson intervals. Deliberately not the paired construction from
  SPEC-007 KD-13 amendment 1: the arms are matched by *form*, not by item, so
  there is no per-item correlation to estimate and claiming one would be the
  same defect that amendment fixed, with the sign reversed.
- **No hypothesis test.** n = 10 per arm cannot reject anything worth rejecting,
  and a p-value here would be a number whose only function is to look like
  evidence.

---

## 6. The three outcomes, named in advance

The owner's three, with the arithmetic that decides each one:

| outcome | reading | decides |
|---|---|---|
| **1 — refuses both** | A ≥ 8/10 **and** B ≥ 8/10 | the corpus is adequate for refusal and the concern was about question construction, as it was for retrieval |
| **2 — refuses B, confabulates on A** | B ≥ 8/10 **and** A ≤ 5/10 | the gap is real and measured rather than assumed, and its size is `B − A` with the interval above |
| **3 — refuses neither** | B ≤ 5/10 | the problem is not the corpus at all and the golden set has a different first job |

**Fourth cell, and it is not a failure of the design:** anything else —
A in 6–7, or B in 6–7 — is **indeterminate**, reported as indeterminate, and
does not get rounded to the nearest named outcome. Naming three outcomes in
advance is worth nothing if a fourth result gets filed under whichever of the
three it most resembles.

### Arm C is why outcome 1 is readable at all — rule 9

Rule 9 says a pre-specified failure mode bound to the primary metric can miss the
failure, because the metric may be insensitive to it. Here the mechanism is
concrete: **`SYSTEM_PROMPT` rule 6 says "declining is a correct outcome, not a
failure", and a model that simply declines a lot produces outcome 1 exactly.**
High refusal on both unanswerable arms is the signature of an adequate corpus
*and* the signature of a refusal-biased pipeline, and refusal rate on arms A and
B cannot tell them apart at any threshold.

So the falsifier is not a threshold on the primary metric. **Arm C is a
different observation point for the same mechanism**: if arm C's refusal rate is
above 2/10, outcome 1 is not available regardless of what A and B do, and the
finding is about the prompt rather than about the corpus.

**Two further mechanisms, with their observation points, neither of them refusal
rate:**

- **A near-miss question that is actually answerable.** Visible in the answer
  text and its citations, not in the rate — an `ANSWERED` verdict here is
  correct behaviour mislabelled as confabulation. Observation point: every
  `ANSWERED` in arm A is read against its cited chunks, and disqualified under
  §3.2 if the citations do answer it.
- **Retrieval never surfaces on-topic chunks for an arm-A question**, so the
  model refuses for arm-B reasons while sitting in arm A. Visible in the
  retrieved `section_path`s, not in the rate. Observation point: the recorded
  paths for every arm-A question are judged on-topic or not, **and that judgement
  is made before its verdict is read.** A near-miss trial that retrieved nothing
  on-topic is not a near-miss trial and is reported separately.

---

## 7. What happens to these questions afterwards

**They stay out of the golden 50 unless the result says they belong in it.**
Authored to probe one mechanism, they are not a sample of anything the golden set
is meant to represent, and folding them in because they exist is how a pilot
quietly becomes an eval set.

The result of this pilot is an input to SPEC-007's open generation-side gate. It
is not itself an answer to that gate, and it is not a corpus decision: if it
comes out at outcome 2, the corpus argument then has evidence behind it and gets
made on its own terms, to the owner, as an amendment.
