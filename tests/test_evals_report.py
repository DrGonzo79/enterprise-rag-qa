"""SPEC-007 AC-1, AC-14, AC-16 and Key decision 13 — the renderer.

**The acceptance test is the retrieval comparison of 2026-08-05**, loaded from
`evals/confirmatory-result.json` rather than retyped, so the test cannot drift
from the finding. It is the hardest artifact this harness is likely to be
handed: b = 3, c = 20, p = 0.000488, seven recorded deviations, and a
`not_a_claim` that took four review rounds and carries both a counter-record
and a statement that the alternative cannot be tested.

**Everything is asserted against the RENDERED STRING, not the model.** A report
test that inspects the report object cannot see a renderer that drops a field —
that is rule 3's sixth and seventh instances, and the whole point of Key
decision 13's acceptance test is that flattening happens at render time.

**Mutation-verified**, each applied to `src/rag_qa/evals/` and reverted, with
what actually failed rather than what was expected to:
  - `_warrant` returns only the claim -> warrant AND truncation tests fail (2).
  - the interval line is dropped -> interval fails (1).
  - deviations render as `len(...)` -> deviations fails (1).
  - `not_a_claim` truncated to 120 chars -> warrant AND truncation fail (2); the
    warrant test catches it because it asserts the *whole* string, which is the
    reason it asserts the whole string.
  - the outcome line is dropped -> outcome fails (1).
  - `outcome` drops the floor and reads only alpha -> inconclusive fails (1).
    This is the mutation that matters most: it is exactly SPEC-004 KD-12's
    original defect, a significant p on too few discordant pairs reported as a
    win.
  - `Warrant` loses its `min_length` -> the cannot-be-constructed test fails (1).

**Key decision 13 amendment 1 (the paired difference) — eight more, and two of
them are worth reading rather than counting:**
  - phi's sign flipped in `newcombe_paired_difference` -> ONLY the narrowing test
    fails. The coverage test passes, and it was written knowing it would: a
    wrong-signed correlation makes the interval *wider*, and a wider interval
    over-covers. **A coverage check cannot catch an error whose direction is
    conservative**, which is why the narrowing property is asserted separately
    rather than trusted to fall out of coverage.
  - phi forced to 0 (the unpaired interval) -> same single failure, same reason.
  - the paired interval halved -> coverage fails (1). This is the mutation that
    establishes the coverage test can fail at all; without it, "coverage >= 0.93"
    is a threshold nobody has seen refused.
  - the difference block dropped from the render -> the difference test fails (1).
  - the interval label reverted to a bare `Interval (95%).` -> 2 fail.
  - the borrowed-interval guard removed -> its test fails (1).
  - `difference_value` computed from `(b - c) / n` rather than from `arms` ->
    2 fail. Note the fixture dependency: on the real table those two expressions
    are **equal** (17/120 either way), so the half of that test which swaps the
    arms is the half doing the work.
  - `construction` loses its `min_length` -> **survived the first pass.** The
    field naming the construction had no test until this mutation was run.
"""

import json
import math
import random
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from scripts.mcnemar import clopper_pearson, newcombe_paired_difference, wilson

from rag_qa.evals.render import render_markdown
from rag_qa.evals.report import (
    ComparisonFigure,
    Corpus,
    Deviation,
    Methodology,
    PairedDifference,
    Preregistration,
    Report,
    ScalarFigure,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULT = REPO_ROOT / "evals" / "confirmatory-result.json"

NOT_A_CLAIM = (
    "What was measured is RRF fusion with the OR fallback, on this corpus, on this mix. "
    "It is not a finding about full-text retrieval: on pilot-2's fourteen lexically "
    "anchored questions hybrid scored recall@8 1.000 against vector-only's 0.857 "
    "(b = 2, c = 0), too small to be conclusive and stated because it is the same lever "
    "on a different question shape and it came out the other way. A different design — "
    "per-term weighting, score-based rather than rank-based fusion, a query parser that "
    "handles out-of-vocabulary terms — is untested, and untested is not refuted. We "
    "cannot test it: the set is unblinded and permanently closed, and a second authoring "
    "cycle is the price of asking again."
)

DIFFERENCE_NOT_A_CLAIM = (
    "Three different parameters are on this figure and this is only one of them: it is "
    "not the 0.87 above it, which is P(vector-only wins | the pair is discordant) and is "
    "about 23 questions rather than 120. It is also not a per-question statement — the "
    "net 17 is 20 minus 3, and hybrid won 3 of the 120 outright, which a difference of "
    "14 points conceals by construction. And it is a property of THIS question mix: the "
    "70/15/15 shape proportions were committed before authoring, the difference is "
    "weighted by them, and on pilot-2's fourteen deliberately lexically-anchored "
    "questions the same difference came out at -0.143, the other way."
)


def _result() -> dict[str, Any]:
    if not RESULT.exists():
        pytest.skip("no confirmatory result committed")
    return json.loads(RESULT.read_text(encoding="utf-8"))


def the_real_report() -> Report:
    """The 2026-08-05 retrieval finding, as a report. Built from the artifact."""
    data = _result()
    primary: dict[str, Any] = data["primary"]
    b, c = int(primary["b_hybrid_only"]), int(primary["c_vector_only"])
    both, neither = int(primary["both"]), int(primary["neither"])
    low, high = clopper_pearson(c, b + c)
    # Arm order below is vector_only first, so the paired table is
    # (both, vector-only-only, hybrid-only, neither) and the difference is
    # positive in vector-only's favour.
    difference_low, difference_high = newcombe_paired_difference(both, c, b, neither)

    difference = PairedDifference(
        interval=(round(difference_low, 4), round(difference_high, 4)),
        construction=(
            f"paired score interval (Newcombe 1998 method 10): the two arms' Wilson "
            f"intervals squared-and-added with the correlation estimated from the paired "
            f"table ({both} both / {c} vector-only / {b} hybrid-only / {neither} neither). "
            f"Not either arm's Wilson interval, and not the discordance split's."
        ),
        claim=(
            f"On the same {primary['n']} questions, vector-only's recall@8 exceeds "
            f"hybrid's by {(c - b) / int(primary['n']):.4g} "
            f"({primary['recall_at_8_vector_only']} against "
            f"{primary['recall_at_8_hybrid']})."
        ),
        not_a_claim=DIFFERENCE_NOT_A_CLAIM,
    )

    comparison = ComparisonFigure(
        difference=difference,
        metric="recall@8",
        arms={
            "vector_only": float(primary["recall_at_8_vector_only"]),
            "hybrid": float(primary["recall_at_8_hybrid"]),
        },
        b=c,  # arm order: the winning arm first, so `b` is its exclusive count
        c=b,
        n=int(primary["n"]),
        test="mcnemar-exact",
        sidedness="two-sided",
        alpha=float(primary["alpha"]),
        p=float(primary["p_two_sided_exact"]),
        interval=(round(low, 4), round(high, 4)),
        corpus_chunks=int(data["corpus_chunks"]),
        git_sha=str(data["git_sha"]),
        claim=(
            f"On {primary['n']} paired questions, hybrid and vector-only disagreed on "
            f"{b + c}; vector-only won {c} of those (McNemar exact, two-sided, "
            f"p = {primary['p_two_sided_exact']})."
        ),
        not_a_claim=NOT_A_CLAIM,
    )
    deviations = [
        Deviation(
            field=str(entry["what"]),
            preregistered="as pre-registered in SPEC-007 KD-12",
            actual=str(entry["what"]),
            reason=str(entry["detail"]),
        )
        for entry in data["deviations"]
    ]
    return Report(
        methodology=Methodology(
            summary=(
                "120 questions authored in a 70/15/15 shape mix committed before authoring, "
                "scored at k = 8 against section labels, with the discordant cases "
                "human-verified."
            ),
            authoring="single-author, not blind — see limitations",
            limitations=[
                "Gold labels are not human-verified except for the 20 discordant cases.",
                "Retrieval only: no generation, no judge, no groundedness.",
                "Single-arm difficulty drifts downward across blocks (Spearman -0.8, p = 0.33).",
            ],
            corpus=Corpus(
                chunks=int(data["corpus_chunks"]),
                documents=["EU AI Act", "NIST AI RMF 1.0", "NVIDIA 10-K FY2026"],
                primary_metric_value=float(primary["recall_at_8_vector_only"]),
            ),
            preregistration=Preregistration(
                preregistered_at="2026-08-02",
                primary_metric="recall@8",
                k=8,
                test="mcnemar-exact",
                sidedness="two-sided",
                alpha=0.05,
                conclusive_when="n_discordant >= 6 AND p < alpha",
            ),
            deviations=deviations,
        ),
        figures=[comparison],
        prompt_version="n/a — retrieval only",
        generator_identity="n/a — retrieval only",
        git_sha=str(data["git_sha"]),
        created_at=str(data["measured_at"]),
    )


# --- Key decision 13: the finding survives rendering --------------------------


def test_the_warrant_survives_rendering() -> None:
    """Both halves, in full. A `claim` without its `not_a_claim` is the shape a
    dropped caveat actually takes, and it renders as a tidier report."""
    rendered = render_markdown(the_real_report())
    assert "**Claim.**" in rendered
    assert "**Not a claim.**" in rendered
    assert NOT_A_CLAIM in rendered


def test_the_not_a_claim_is_not_truncated() -> None:
    """Four review rounds went into this text and the last clause is the one a
    summariser would drop: that the alternative *cannot* be tested."""
    rendered = render_markdown(the_real_report())
    assert "untested is not refuted" in rendered
    assert "a second authoring cycle is the price of asking again" in rendered
    assert "pilot-2" in rendered, "the counter-record must survive"


def test_the_interval_is_rendered_beside_the_proportion() -> None:
    """20 of 23 without an interval reads as 0.87 exactly."""
    rendered = render_markdown(the_real_report())
    assert "**Interval (95%) on P(" in rendered
    low, high = clopper_pearson(20, 23)
    assert f"{low:.4g}" in rendered and f"{high:.4g}" in rendered


def test_the_outcome_is_stated_not_inferred() -> None:
    """`arms` alone lets a reader infer a winner; SPEC-004 KD-12's correction was
    that a 2-1 split had been reported as one."""
    rendered = render_markdown(the_real_report())
    assert "**Outcome.** vector_only" in rendered


def test_every_deviation_is_rendered_in_full_with_its_reason() -> None:
    """A count would hide seven judgement calls behind the digit 7."""
    report = the_real_report()
    rendered = render_markdown(report)
    assert len(report.methodology.deviations) == 7
    for deviation in report.methodology.deviations:
        assert deviation.field in rendered
        assert deviation.reason in rendered


def test_the_corpus_gate_is_derived_not_declared() -> None:
    """Key decision 7 amendment 1: the property, demonstrated by the measurement.

    0.9167 is unsaturated and the corpus never grew — a harder question set
    reached the gate that the corpus ladder was going to be built for.
    """
    report = the_real_report()
    assert report.methodology.corpus.desaturated is True
    assert "de-saturated: true" in render_markdown(report)
    assert "derived, not declared" in render_markdown(report)


# --- Key decision 13 amendment 1: the difference is its own parameter ----------


def test_the_paired_difference_is_rendered_with_its_own_interval_and_warrant() -> None:
    """The sentence a reader carries away is "0.9167 against 0.775, so fourteen
    points", and before this it was the only quantity on the figure published
    without an interval of its own."""
    report = the_real_report()
    rendered = render_markdown(report)
    difference = report.figures[0].difference  # type: ignore[union-attr]

    assert "#### Difference: vector_only minus hybrid" in rendered
    assert "**+0.1417**" in rendered
    assert f"[{difference.interval[0]:.4g}, {difference.interval[1]:.4g}]" in rendered
    assert difference.construction in rendered
    assert DIFFERENCE_NOT_A_CLAIM in rendered
    # Two warrants on this figure, not one: the test's and the difference's.
    assert rendered.count("**Not a claim.**") == 2


def test_every_interval_on_the_figure_names_its_parameter() -> None:
    """Two intervals on two different parameters. A reader who meets
    `Interval (95%)` twice attaches whichever is nearer to whatever they were
    reading, which is how the split's 0.87 becomes an interval on the difference.
    """
    rendered = render_markdown(the_real_report())
    assert "**Interval (95%) on P(vector_only wins | the pair is discordant).**" in rendered
    # The bare label is what this replaced; it must not survive anywhere.
    assert "**Interval (95%).**" not in rendered


def test_the_paired_interval_is_none_of_the_intervals_already_in_the_report() -> None:
    """The defect was a derived quantity carrying someone else's interval, so the
    assertion is against every interval that was already available to borrow."""
    paired = newcombe_paired_difference(90, 20, 3, 7)
    borrowable = [
        clopper_pearson(20, 23),  # the discordance split
        wilson(110, 120),  # vector-only's own
        wilson(93, 120),  # hybrid's own
    ]
    for other in borrowable:
        assert not (
            math.isclose(paired[0], other[0], abs_tol=1e-3)
            and math.isclose(paired[1], other[1], abs_tol=1e-3)
        ), f"the paired interval {paired} coincides with {other}"
    assert paired[0] > 0, "the interval excludes zero, which is what the test also found"


def test_pairing_narrows_the_interval_and_that_is_what_pairing_buys() -> None:
    """The correlation enters with a factor of -2, so a positive correlation
    narrows. Setting it to zero is the unpaired interval: wider, and answering a
    question nobody asked.

    This is the assertion that catches a sign error on phi, which the coverage
    check below structurally cannot — a too-wide interval over-covers.
    """
    paired = newcombe_paired_difference(90, 20, 3, 7)
    # phi = 0 exactly when one product term vanishes; b*c = a*d gives phi = 0
    # without touching the marginals' Wilson intervals only in contrived tables,
    # so compare against the explicit square-and-add instead.
    p1, p2 = 110 / 120, 93 / 120
    lower1, upper1 = wilson(110, 120)
    lower2, upper2 = wilson(93, 120)
    unpaired = (
        (p1 - p2) - math.hypot(p1 - lower1, upper2 - p2),
        (p1 - p2) + math.hypot(upper1 - p1, p2 - lower2),
    )
    assert paired[0] > unpaired[0] and paired[1] < unpaired[1]


def test_the_paired_interval_covers_at_its_nominal_rate() -> None:
    """Simulated coverage, because self-consistency does not make a formula right.

    Cell probabilities are the observed table's, so the check runs where the
    figure runs rather than somewhere the method is known to behave.
    """
    rng = random.Random(20260807)
    cells = (90 / 120, 20 / 120, 3 / 120, 7 / 120)
    truth = cells[1] - cells[2]
    covered = 0
    reps = 2000
    for _ in range(reps):
        counts = [0, 0, 0, 0]
        for _ in range(120):
            counts[_multinomial_draw(rng, cells)] += 1
        low, high = newcombe_paired_difference(*counts)
        covered += low <= truth <= high
    coverage = covered / reps
    # The lower bound allows Monte Carlo error (SE ~ 0.005); the upper bound is
    # what catches an interval that is merely too wide to be wrong.
    assert 0.93 <= coverage <= 0.995, coverage


def _multinomial_draw(rng: random.Random, probabilities: tuple[float, ...]) -> int:
    draw = rng.random()
    cumulative = 0.0
    for index, probability in enumerate(probabilities):
        cumulative += probability
        if draw < cumulative:
            return index
    return len(probabilities) - 1


def test_a_difference_interval_copied_from_the_split_cannot_be_constructed() -> None:
    """The literal form of the defect. Stated as the narrow check it is: a merely
    *wrong* paired interval passes this one.

    Deliberately NOT built from the real figure, where the difference (0.1417)
    falls outside the split's interval (0.6641, 0.9722) and the containment check
    fires first — a fixture where the guard under test is unreachable is rule 3's
    recurring shape. This table's difference sits inside the split's interval, so
    only the equality guard can reject it.
    """
    split = (0.1, 0.9)
    borrowed = PairedDifference(interval=split, construction="borrowed", claim="c", not_a_claim="n")
    with pytest.raises(ValidationError, match="different parameters"):
        ComparisonFigure(
            metric="recall@8",
            arms={"a": 0.9, "b": 0.1},  # difference 0.8, inside `split`
            b=20,
            c=3,
            n=120,
            test="mcnemar-exact",
            sidedness="two-sided",
            alpha=0.05,
            p=0.0005,
            interval=split,
            difference=borrowed,
            corpus_chunks=358,
            git_sha="abc",
            claim="c",
            not_a_claim="n",
        )


def test_the_difference_is_derived_from_the_arms_not_declared() -> None:
    """Same property as `outcome`: a producer that states its own difference can
    state one its arms do not support."""
    figure = the_real_report().figures[0]
    assert isinstance(figure, ComparisonFigure)
    assert figure.difference_value == pytest.approx(0.9167 - 0.775)
    with pytest.raises(ValidationError, match="lies outside its interval"):
        ComparisonFigure(
            **{
                **figure.model_dump(
                    exclude={"arms", "n_discordant", "outcome", "difference_value"}
                ),
                "arms": {"vector_only": 0.775, "hybrid": 0.9167},
            }
        )


# --- AC-1 / AC-14 / AC-16: the structure, not this finding ---------------------


def test_a_figure_without_a_warrant_cannot_be_constructed() -> None:
    """AC-1 as a property of the type. Not "fails validation" — cannot exist."""
    for missing in ({"claim": ""}, {"not_a_claim": ""}):
        with pytest.raises(ValidationError):
            ScalarFigure(
                metric="refusal_rate",
                value=0.86,
                n=22,
                decided=22,
                interval=(0.65, 0.97),
                corpus_chunks=358,
                git_sha="abc",
                **{"claim": "c", "not_a_claim": "n", **missing},  # type: ignore[arg-type]
            )
    # The difference carries three required strings, not two: an interval whose
    # construction is unnamed is the one a reader assumes came from the two
    # intervals above it.
    for absent in ({"claim": ""}, {"not_a_claim": ""}, {"construction": ""}):
        with pytest.raises(ValidationError):
            PairedDifference(
                interval=(0.0, 1.0),
                **{  # type: ignore[arg-type]
                    "construction": "paired score interval",
                    "claim": "c",
                    "not_a_claim": "n",
                    **absent,
                },
            )


def test_inconclusive_is_a_result_and_cannot_be_overridden() -> None:
    """Below the floor, or p >= alpha, the outcome is `inconclusive` — computed.

    Either condition alone suffices: a significant p on four discordant pairs is
    a number the pre-registration already refused.
    """
    base = {
        "metric": "recall@8",
        "arms": {"hybrid": 0.9, "vector_only": 0.8},
        "n": 130,
        "test": "mcnemar-exact",
        "sidedness": "two-sided",
        "alpha": 0.05,
        "interval": (0.1, 0.9),
        "difference": PairedDifference(
            interval=(0.02, 0.18), construction="stub", claim="c", not_a_claim="n"
        ),
        "corpus_chunks": 358,
        "git_sha": "abc",
        "claim": "c",
        "not_a_claim": "n",
    }
    below_floor = ComparisonFigure(b=2, c=1, p=0.001, **base)  # type: ignore[arg-type]
    assert below_floor.outcome == "inconclusive"
    not_significant = ComparisonFigure(b=12, c=11, p=0.6, **base)  # type: ignore[arg-type]
    assert not_significant.outcome == "inconclusive"
    conclusive = ComparisonFigure(b=20, c=3, p=0.0005, **base)  # type: ignore[arg-type]
    assert conclusive.outcome == "hybrid"


def test_a_comparison_contradicting_its_own_preregistration_does_not_render() -> None:
    """AC-14: a substitution the reader cannot see is the failure this prevents."""
    report = the_real_report()
    swapped = report.figures[0].model_dump()
    swapped["sidedness"] = "one-sided"
    swapped.pop("n_discordant", None)
    swapped.pop("outcome", None)
    with pytest.raises(ValidationError):
        Report(
            **{
                **report.model_dump(exclude={"figures", "methodology"}),
                "methodology": report.methodology.model_copy(update={"deviations": []}),
                "figures": [ComparisonFigure(**swapped)],
            }
        )
