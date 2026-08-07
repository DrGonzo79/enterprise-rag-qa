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
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from scripts.mcnemar import clopper_pearson

from rag_qa.evals.render import render_markdown
from rag_qa.evals.report import (
    ComparisonFigure,
    Corpus,
    Deviation,
    Methodology,
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


def _result() -> dict[str, Any]:
    if not RESULT.exists():
        pytest.skip("no confirmatory result committed")
    return json.loads(RESULT.read_text(encoding="utf-8"))


def the_real_report() -> Report:
    """The 2026-08-05 retrieval finding, as a report. Built from the artifact."""
    data = _result()
    primary: dict[str, Any] = data["primary"]
    b, c = int(primary["b_hybrid_only"]), int(primary["c_vector_only"])
    low, high = clopper_pearson(c, b + c)

    comparison = ComparisonFigure(
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
    assert "**Interval (95%)." in rendered
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
