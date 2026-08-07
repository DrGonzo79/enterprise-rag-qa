"""The eval report: figures that cannot exist without their warrant (SPEC-007).

**AC-1 is enforced by the type, not by the author.** A figure without a `claim`
and a `not_a_claim` is not a figure that fails validation later — it is a value
that cannot be constructed. That is the difference between a rule and a habit,
and it is why this module exists before the golden set does (Key decision 13).

**The second structural property is that `outcome` and `desaturated` are
DERIVED.** A producer that declares itself conclusive is grading its own
pre-registration, and a producer that declares itself de-saturated is grading
its own prerequisite (Key decision 7, amendment 1). Both are computed here from
the numbers beside them, so the only way to change them is to change the data.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

# The floor below which no split can reach alpha (SPEC-007 KD-12 amendment 1).
MIN_DISCORDANT_FOR_ANY_REJECTION = 6

Warrant = Annotated[str, Field(min_length=1)]


class Warranted(BaseModel):
    """Every figure carries what it does and does not license. AC-1.

    `min_length=1` on both is the whole mechanism: an empty string is the shape
    a dropped warrant actually takes, and a figure that renders `claim: ""` is
    indistinguishable from one that never had a claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: Warrant
    not_a_claim: Warrant
    corpus_chunks: int = Field(gt=0)
    git_sha: str = Field(min_length=1)


class ScalarFigure(Warranted):
    """A single measured proportion, with its denominator and its interval."""

    kind: Literal["scalar"] = "scalar"
    metric: str = Field(min_length=1)
    value: float
    n: int = Field(gt=0)
    decided: int = Field(ge=0)
    # Wilson or Clopper-Pearson, 95%. NOT optional: a proportion rendered
    # without its interval reads as certainty, which is the specific way a
    # figure misleads while every number in it is true.
    interval: tuple[float, float]

    @model_validator(mode="after")
    def _decided_within_n(self) -> ScalarFigure:
        if self.decided > self.n:
            raise ValueError(f"decided {self.decided} exceeds n {self.n}")
        low, high = self.interval
        if not low <= self.value <= high:
            raise ValueError(f"value {self.value} lies outside its interval {self.interval}")
        return self


class ArmValue(BaseModel):
    """One arm's own value, with its own interval (Key decision 13 amendment 2).

    **This type exists because `arms: dict[str, float]` rendered two bare
    proportions directly above an interval belonging to the discordance split**,
    and borrowing-by-adjacency is the same defect `PairedDifference` was added to
    fix, one row up. It also violated Key decision 13's own stated rule — *every
    proportion prints its interval beside it* — on the page that states it.

    `interval` is **required**, and for a metric that is not a proportion it is
    whatever construction the producer actually used, named in
    `ComparisonFigure.arm_interval_construction`. Requiring it is the point: a
    producer with no interval to give has not finished measuring.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    interval: tuple[float, float]

    @model_validator(mode="after")
    def _value_within_interval(self) -> ArmValue:
        low, high = self.interval
        if not low <= self.value <= high:
            raise ValueError(f"arm value {self.value} lies outside its interval {self.interval}")
        return self


class PairedDifference(BaseModel):
    """The difference between the arms, with its OWN interval and its own warrant.

    **Why this is a required field rather than a nicety.** A comparison figure
    renders three quantities a reader can read as "the effect", and they are
    three different parameters:

    | rendered | parameter |
    |---|---|
    | each arm's value | that arm's marginal rate |
    | `interval` | P(the winning arm wins \\| the pair is discordant) |
    | this | the paired difference between the arms |

    The sentence a reader carries away is the third one — *0.9167 against 0.775,
    so fourteen points* — and before this field existed that was the only one of
    the three published **without an interval of its own**, inside the figure type
    written to make exactly that impossible (Key decision 1). It sat next to the
    split's interval and borrowed it by adjacency.

    `construction` is required and free text because the right construction
    depends on the metric: for paired binary outcomes it is a paired score
    interval, and for anything else it is whatever the producer actually did.
    Naming it is the point — an unnamed interval on a difference is the one a
    reader assumes came from the two intervals above it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    interval: tuple[float, float]
    construction: str = Field(min_length=1)
    claim: Warrant
    not_a_claim: Warrant


class ComparisonFigure(Warranted):
    """A paired comparison with the pre-registered test's result. AC-16.

    `outcome` is a computed field rather than an input. SPEC-004 Key decision
    12's whole correction was that a 2-1 split of three decided questions had
    been reported as a win; letting a producer *state* the outcome would leave
    that available.
    """

    kind: Literal["comparison"] = "comparison"
    metric: str = Field(min_length=1)
    arms: dict[str, ArmValue] = Field(min_length=2)
    # How each arm's interval was built. Separate from the difference's, because
    # they are different constructions on different parameters and one string
    # covering both would be the conflation this figure keeps having to prevent.
    arm_interval_construction: str = Field(min_length=1)
    b: int = Field(ge=0)
    c: int = Field(ge=0)
    n: int = Field(gt=0)
    test: str = Field(min_length=1)
    sidedness: Literal["two-sided", "one-sided"]
    alpha: float = Field(gt=0, lt=1)
    p: float = Field(ge=0, le=1)
    # The interval on the parameter the test is actually about: P(arm A wins |
    # the pair is discordant). Reporting b and c without it invites 20/23 to be
    # read as 0.87 exactly. It is NOT an interval on the difference between the
    # arms — that is `difference`, and conflating them is the reason both are
    # rendered with their parameter named.
    interval: tuple[float, float]
    difference: PairedDifference
    floor: int = MIN_DISCORDANT_FOR_ANY_REJECTION

    @computed_field
    @property
    def n_discordant(self) -> int:
        return self.b + self.c

    @computed_field
    @property
    def difference_value(self) -> float:
        """Derived from `arms`, never declared, for the same reason `outcome` is.

        A producer that states its own difference can state one its arms do not
        support, and the difference is the number the headline sentence is made
        of.
        """
        first, second = list(self.arms)
        return self.arms[first].value - self.arms[second].value

    @computed_field
    @property
    def outcome(self) -> str:
        """`inconclusive` is a result, not a missing one.

        Derived from the floor and alpha together, because either one failing
        alone is enough: a significant p on four discordant pairs is a number
        the pre-registration already refused to accept.
        """
        if self.n_discordant < self.floor or self.p >= self.alpha:
            return "inconclusive"
        winners = sorted(self.arms, key=lambda name: self.arms[name].value, reverse=True)
        return winners[0]

    @model_validator(mode="after")
    def _pairs_fit_the_denominator(self) -> ComparisonFigure:
        if self.b + self.c > self.n:
            raise ValueError(f"discordant pairs {self.b + self.c} exceed n {self.n}")
        return self

    @model_validator(mode="after")
    def _the_difference_is_its_own(self) -> ComparisonFigure:
        """Two checks, and the second is the one this field was added for.

        The first is ordinary: an interval that does not contain the difference
        it is attached to is a transcription error.

        The second rejects a `difference.interval` **equal to the split's**. That
        is the literal form of the defect — a derived quantity carrying someone
        else's interval — and it is the form a copy-paste takes. It catches only
        the identical copy, which is stated here rather than left to be inferred:
        a merely *wrong* paired interval passes this and is caught, if at all, by
        `construction` being read.
        """
        low, high = self.difference.interval
        if not low <= self.difference_value <= high:
            raise ValueError(
                f"difference {self.difference_value} lies outside its interval "
                f"{self.difference.interval}"
            )
        if self.difference.interval == self.interval:
            raise ValueError(
                "difference.interval equals the discordance split's interval; these are "
                "different parameters and one cannot stand in for the other"
            )
        return self


Figure = ScalarFigure | ComparisonFigure


class Deviation(BaseModel):
    """A departure from the pre-registration, with its reason. AC-14."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    preregistered: str = Field(min_length=1)
    actual: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Corpus(BaseModel):
    """`desaturated` is computed, never declared (Key decision 7 amendment 1).

    The gate is the **property** — the reported metric must be unsaturated on
    the set it is reported against — not the lever that reached it. A harder
    question set satisfies it exactly as a larger corpus would.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunks: int = Field(gt=0)
    documents: list[str] = Field(min_length=1)
    primary_metric_value: float = Field(ge=0, le=1)
    # Key decision 13 amendment 2. `desaturated` is a THRESHOLDED CLAIM resting
    # on a point estimate, and it was rendered bare -- so the claim was being
    # made at the estimate and nowhere else. Required, because the interesting
    # case is the one where the estimate clears 1.0 and the bound does not.
    primary_metric_interval: tuple[float, float]

    @model_validator(mode="after")
    def _value_within_interval(self) -> Corpus:
        low, high = self.primary_metric_interval
        if not low <= self.primary_metric_value <= high:
            raise ValueError(
                f"primary metric {self.primary_metric_value} lies outside its interval "
                f"{self.primary_metric_interval}"
            )
        return self

    @computed_field
    @property
    def desaturated(self) -> bool:
        return self.primary_metric_value < 1.0

    @computed_field
    @property
    def desaturated_at_the_bound(self) -> bool:
        """Whether the gate survives the interval's upper end, not just the point.

        Separate from `desaturated` rather than replacing it, because they are
        different claims and collapsing them would hide which one is being made:
        the gate is stated on the measurement, and this says whether the
        measurement's own uncertainty could reach saturation.
        """
        return self.primary_metric_interval[1] < 1.0


class Preregistration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preregistered_at: str = Field(min_length=1)
    primary_metric: str = Field(min_length=1)
    k: int = Field(gt=0)
    test: str = Field(min_length=1)
    sidedness: Literal["two-sided", "one-sided"]
    alpha: float = Field(gt=0, lt=1)
    conclusive_when: str = Field(min_length=1)


class Methodology(BaseModel):
    """The report carries its own methodology; SPEC-009 renders it to a reader
    who has not opened the repository (Key decision 2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)
    authoring: str = Field(min_length=1)
    # Key decision 3's bound, RENDERED rather than filed. An empty list is
    # rejected: a study with no limitations has not looked for any.
    limitations: list[str] = Field(min_length=1)
    corpus: Corpus
    preregistration: Preregistration
    deviations: list[Deviation]


class Report(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    methodology: Methodology
    figures: list[Figure] = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    generator_identity: str = Field(min_length=1)
    git_sha: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    cost_usd: str = "0"

    @model_validator(mode="after")
    def _figures_agree_with_the_preregistration(self) -> Report:
        """A report whose comparison contradicts its own pre-registration does
        not render (AC-14). Recording a deviation is what makes it render.
        """
        prereg = self.methodology.preregistration
        declared = {d.field for d in self.methodology.deviations}
        for figure in self.figures:
            if not isinstance(figure, ComparisonFigure):
                continue
            for name, mine, theirs in (
                ("test", figure.test, prereg.test),
                ("sidedness", figure.sidedness, prereg.sidedness),
                ("alpha", figure.alpha, prereg.alpha),
            ):
                if mine != theirs and name not in declared:
                    raise ValueError(
                        f"comparison {name}={mine!r} differs from the pre-registered "
                        f"{theirs!r} and no deviation records it"
                    )
        return self
