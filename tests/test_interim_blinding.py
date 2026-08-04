"""SPEC-007 AC-17 — the interim sizing look cannot see which arm won.

The load-bearing test here is `test_summary_is_invariant_under_swapping_the_arms`.
It is stated as an **invariance** rather than as a list of forbidden fields
because a denylist only forecloses the leaks somebody thought of; an invariance
forecloses every channel at once — a field, a rounding, an ordering, a count
that happens to correlate with direction.

The allowlist tests are the backstop, and they fail *closed*: a key nobody
listed is a failure whether or not a reviewer judges it to encode direction.

**Mutation-verified** (CLAUDE.md rule 3), each mutation applied and reverted,
with what actually failed rather than what was expected to:
  - `case_record` leaks both ranks and `summarise` counts `hybrid_only` ->
    invariance, allowlist and per-case-keys all FAIL (3).
  - `case_record` leaks `hybrid_rank` alone -> invariance and per-case-keys
    FAIL (2). Invariance catches it because the mirror swaps the arms, so the
    leaked field differs between a run and its mirror.
  - `discordant` computed as `_hit(a) and not _hit(b)` -> invariance and the
    count test FAIL (2). That expression is asymmetric, which is exactly what
    invariance is looking for; the count test pins the arithmetic separately.
  - `min_discordant_for_power` takes the first power crossing instead of the
    sustained one -> the non-monotonicity test FAILS (1), at 15.
"""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts.interim_r import (
    BLOCK_1_REFERENCE_MRR,
    COMMITTED_BLOCK_MIX,
    DIFFICULTY_BAND,
    N_CAP,
    case_record,
    check_mix,
    drift_breach,
    single_arm_difficulty,
    sizing,
    stopping_state,
    summarise,
)
from scripts.mcnemar import (
    clopper_pearson,
    mcnemar_exact_two_sided,
    min_discordant_for_power,
    power,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SUMMARY_KEYS = {"n", "n_discordant", "r", "shape_composition"}
CASE_KEYS = {"id", "shape", "discordant"}


def pipeline(pairs: Sequence[tuple[int | None, int | None]]) -> str:
    """The whole interim reduction, as the artifact would serialise it."""
    records = [
        case_record(f"con-{i:03d}", "natural-language", a, b) for i, (a, b) in enumerate(pairs, 1)
    ]
    return json.dumps({"per_case": records, "summary": summarise(records)}, sort_keys=True)


def test_summary_is_invariant_under_swapping_the_arms() -> None:
    """A maximally lopsided split and its mirror must be indistinguishable.

    9 hybrid-only against 1 vector-only is the shape that would decide the
    comparison if it appeared at the interim, so it is the shape a leak has the
    most to leak about. If the two renderings differ by a single byte, the
    interim can see the effect and the nuisance-parameter argument in
    Key decision 12 amendment 5 does not hold.
    """
    lopsided: Sequence[tuple[int | None, int | None]] = (
        [(1, None)] * 9 + [(None, 1)] * 1 + [(2, 3)] * 10 + [(None, None)] * 10
    )
    mirrored = [(b, a) for a, b in lopsided]

    assert pipeline(lopsided) == pipeline(mirrored)


def test_a_leaked_split_would_have_been_caught() -> None:
    """The invariance test can fail — shown here rather than asserted about.

    Rule 3's whole subject is assertions that hold for a reason unrelated to
    the behaviour. This pins the invariance test's sensitivity by handing it a
    summariser that *does* leak, so `test_summary_is_invariant...` passing is
    evidence about `case_record` rather than about `json.dumps`.
    """

    def leaky(pairs: Sequence[tuple[int | None, int | None]]) -> str:
        hybrid_only = sum(1 for a, b in pairs if a is not None and b is None)
        return json.dumps({"hybrid_only": hybrid_only})

    lopsided: Sequence[tuple[int | None, int | None]] = [(1, None)] * 9 + [(None, 1)]
    assert leaky(lopsided) != leaky([(b, a) for a, b in lopsided])


def test_summary_keys_are_an_allowlist() -> None:
    records = [case_record("con-001", "natural-language", 1, None)]
    assert set(summarise(records)) == SUMMARY_KEYS


def test_case_records_carry_no_rank_and_no_per_arm_hit() -> None:
    record = case_record("con-001", "citation-anchored", 3, None)
    assert set(record) == CASE_KEYS
    assert record["discordant"] is True


def test_discordance_counts_pairs_in_both_directions() -> None:
    """`hit_a != hit_b`, not `hit_a and not hit_b`.

    The invariance test also rejects the one-directional form, because that
    expression is asymmetric in its arguments. This one pins the *arithmetic*
    rather than the symmetry: a count that is wrong by a constant would still
    be perfectly blind, and `r` is the number the owner is sizing against.
    """
    records = [
        case_record("a", "natural-language", 1, None),  # one arm only
        case_record("b", "natural-language", None, 1),  # the other arm only
        case_record("c", "natural-language", 1, 2),  # both
        case_record("d", "natural-language", None, None),  # neither
    ]
    assert summarise(records)["n_discordant"] == 2


def test_a_miss_beyond_k_is_not_a_hit() -> None:
    """Rank 9 with k = 8 is a miss; discordance is defined at k, not at any rank."""
    assert case_record("a", "natural-language", 9, None)["discordant"] is False
    assert case_record("b", "natural-language", 8, None)["discordant"] is True


def test_sizing_output_carries_no_direction() -> None:
    plan = sizing(4, 30)
    assert set(plan) == {
        "theta_assumed",
        "required_discordant",
        "r_point",
        "r_ci95",
        "N_at_r_point",
        "N_at_r_ci_low",
        "N_at_r_ci_high",
    }
    assert plan["r_point"] == pytest.approx(0.1333, abs=1e-4)
    # A rate of zero makes N undefined rather than large -- the distinction
    # KD-12 amendment 1 was written to preserve.
    assert sizing(0, 30)["N_at_r_point"]["floor"] is None


def test_the_interim_refuses_a_block_that_is_not_the_committed_mix() -> None:
    """A block that is not the mix estimates `r` for the wrong population.

    That is the shape-mix-from-the-data error arriving through the back door,
    so it is refused by the tooling rather than noticed in review.
    """
    committed = COMMITTED_BLOCK_MIX[1]
    good = [
        {"id": f"con-{i:03d}", "shape": shape}
        for shape, count in committed.items()
        for i in range(count)
    ]
    assert check_mix(good, 1) is None

    all_natural = [{"id": f"con-{i:03d}", "shape": "natural-language"} for i in range(30)]
    problem = check_mix(all_natural, 1)
    assert problem is not None and "composition" in problem


def test_the_blocks_compose_to_the_cap_at_exactly_70_15_15() -> None:
    """Six blocks of 30 plus one of 20 make 200 at 140/30/30 — no rounding left.

    Under amendment 7 the design is inverse sampling with a cap, so the blocks
    have to compose to the *cap* rather than to a fixed 150. The alternation
    that made 150 land on 105/23/22 lands the cap on exact 70/15/15, which is a
    better property than the one it replaced: at the cap there is no residue at
    all, and at every earlier boundary the mix is off by at most one question.
    """
    totals: dict[str, int] = {}
    for block, mix in COMMITTED_BLOCK_MIX.items():
        assert sum(mix.values()) == (30 if block <= 6 else 20)
        for shape, count in mix.items():
            totals[shape] = totals.get(shape, 0) + count
    assert sum(totals.values()) == N_CAP == 200
    assert totals == {"natural-language": 140, "citation-anchored": 30, "cross-section": 30}
    assert totals["natural-language"] / N_CAP == 0.70
    assert totals["citation-anchored"] / N_CAP == 0.15


def test_the_stopping_rule_reports_questions_not_expectations() -> None:
    """The failure amendment 7 corrects: reading E[n_discordant] as the count.

    A fixed N = 150 at r = 0.15 has mean 22.5 pairs and reaches 23 about half
    the time. The rule therefore counts pairs and reports questions remaining,
    and it never claims a target has been met on the strength of an average.
    """
    mid = stopping_state(60, 9)
    assert mid["discordant_remaining"] == 14
    assert mid["expected_questions_remaining"] == 94  # ceil(14 / 0.15)
    assert mid["questions_remaining_to_cap"] == 140
    assert mid["verdict"].startswith("CONTINUE")

    assert stopping_state(150, 23)["verdict"].startswith("STOP")
    # Overshoot is fine and only adds power; it must not read as an error.
    assert stopping_state(180, 27)["discordant_remaining"] == 0
    # The cap binds and the result is reported as underpowered, not as a stop.
    capped = stopping_state(200, 19)
    assert capped["verdict"].startswith("STOP: cap")
    assert "underpowered" in capped["verdict"]


def test_the_stopping_rule_survives_a_zero_rate() -> None:
    """`expected_questions_remaining` is undefined at r = 0, not infinite.

    The same distinction KD-12 amendment 1 was written to preserve: a rate of
    zero makes the projection undefined rather than large, and a number here
    would be a fabrication in the direction that flatters the plan.
    """
    assert stopping_state(30, 0)["expected_questions_remaining"] is None
    assert stopping_state(30, 0)["questions_remaining_to_cap"] == 170


def test_the_floor_of_six_is_derived_not_chosen() -> None:
    assert mcnemar_exact_two_sided(5, 0) >= 0.05
    assert mcnemar_exact_two_sided(6, 0) < 0.05


def test_power_is_not_monotone_in_n_so_sizing_uses_the_sustained_crossing() -> None:
    """The correction recorded in KD-12 amendment 5.

    12 and 20 were published as the discordant counts buying power 0.5 and 0.8.
    Both are *first* crossings that power falls back below one or two pairs
    later, because the critical value of a discrete test moves in steps. This
    pins the fact that made them wrong, not merely the replacements.
    """
    assert power(12, 0.8) >= 0.5
    assert power(14, 0.8) < 0.5
    assert power(20, 0.8) >= 0.8
    assert power(22, 0.8) < 0.8

    assert min_discordant_for_power(0.5, 0.8) == 15
    assert min_discordant_for_power(0.8, 0.8) == 23


def test_clopper_pearson_matches_the_published_pilot_interval() -> None:
    low, high = clopper_pearson(2, 14)
    assert (round(low, 3), round(high, 3)) == (0.018, 0.428)
    assert clopper_pearson(0, 26)[0] == 0.0


# --- The single-arm difficulty proxy (amendment 6) ---------------------------


def emitted(
    pairs: Sequence[tuple[int | None, int | None]],
) -> str:
    """Everything the interim artifact exposes about outcomes, as one string.

    Per-case discordance, the block summary, and the single-arm proxy — the
    three things that are published together. If two different splits produce
    the same string, the artifact does not determine the split.
    """
    records = [
        case_record(f"con-{i:03d}", "natural-language", a, b) for i, (a, b) in enumerate(pairs, 1)
    ]
    return json.dumps(
        {
            "per_case": records,
            "summary": summarise(records),
            "difficulty": single_arm_difficulty([b for _, b in pairs]),
        },
        sort_keys=True,
    )


def test_the_published_artifact_does_not_determine_the_split() -> None:
    """Two different (b, c) splits, one identical artifact.

    This replaces arm-swap invariance as the guarantee for the *artifact*,
    because the difficulty proxy is single-arm by construction and therefore
    cannot be invariant under swapping the arms — swapping is exactly what it
    measures. The property that survives, and the one that matters, is that the
    published quantities leave the split underdetermined.

    The algebra behind it: the artifact fixes `n`, `n_discordant = b + c` and
    `vector_hits = both + c`. That is three equations in four unknowns
    (b, c, both, neither), so `c` is free across its whole feasible range and
    every value of it produces the same artifact. Below is one instance —
    (b, c) = (7, 0) against (4, 3) — checked byte for byte.
    """
    # 7 hybrid-only, 0 vector-only, 13 both, 10 neither. vector hits = 13.
    split_a: Sequence[tuple[int | None, int | None]] = (
        [(1, None)] * 7 + [(1, 1)] * 13 + [(None, None)] * 10
    )
    # 4 hybrid-only, 3 vector-only, 10 both, 13 neither. vector hits = 13.
    split_b: Sequence[tuple[int | None, int | None]] = (
        [(1, None)] * 4 + [(None, 1)] * 3 + [(1, 1)] * 10 + [(None, None)] * 13
    )
    assert len(split_a) == len(split_b) == 30

    assert emitted(split_a) == emitted(split_b)


def test_the_indistinguishability_test_can_fail() -> None:
    """Shown, not assumed — the sensitivity check rule 3 keeps asking for.

    Adding the *hybrid* arm's recall to the artifact makes the two splits
    distinguishable immediately, which is why the proxy is single-arm and why
    that is a constraint rather than a preference.
    """

    def with_both_arms(pairs: Sequence[tuple[int | None, int | None]]) -> str:
        hybrid_hits = sum(1 for a, _ in pairs if a is not None)
        return json.dumps({"hybrid_recall": hybrid_hits / len(pairs)})

    split_a: Sequence[tuple[int | None, int | None]] = (
        [(1, None)] * 7 + [(1, 1)] * 13 + [(None, None)] * 10
    )
    split_b: Sequence[tuple[int | None, int | None]] = (
        [(1, None)] * 4 + [(None, 1)] * 3 + [(1, 1)] * 10 + [(None, None)] * 13
    )
    assert with_both_arms(split_a) != with_both_arms(split_b)


def test_the_proxy_reports_no_per_case_outcome() -> None:
    """Aggregate only. A per-case vector outcome beside a per-case discordance
    flag identifies the split one question at a time."""
    proxy = single_arm_difficulty([1, 3, None, 8, 9])
    assert set(proxy) == {"arm", "recall_at_8", "mrr_at_8", "gold_rank_histogram"}
    assert proxy["arm"] == "vector-only"
    # rank 9 is beyond k and counts as a miss, like everywhere else
    assert proxy["recall_at_8"] == pytest.approx(0.6)
    assert proxy["gold_rank_histogram"]["miss"] == 2


def test_mrr_separates_blocks_that_recall_cannot() -> None:
    """Why the proxy carries two numbers rather than one.

    Both blocks below have identical `recall@8`; one has every gold chunk at
    rank 1 and the other at rank 8. Drift of that kind is exactly what a
    coarse proxy would miss, so the finer one is reported beside it.
    """
    top = single_arm_difficulty([1, 1, 1, 1])
    bottom = single_arm_difficulty([8, 8, 8, 8])
    assert top["recall_at_8"] == bottom["recall_at_8"]
    assert top["mrr_at_8"] > bottom["mrr_at_8"]


def test_the_drift_band_is_committed_and_two_sided() -> None:
    """A block that gets *easier* is drift too.

    The mechanism amendment 6 names is an author getting better at writing
    discriminating questions, which moves difficulty up. Banding only that
    direction would assume the mechanism is the only one there is.
    """
    assert drift_breach(BLOCK_1_REFERENCE_MRR, 2) is None
    assert drift_breach(BLOCK_1_REFERENCE_MRR + DIFFICULTY_BAND, 2) is None
    assert drift_breach(BLOCK_1_REFERENCE_MRR + DIFFICULTY_BAND + 0.001, 2) is not None
    assert drift_breach(BLOCK_1_REFERENCE_MRR - DIFFICULTY_BAND - 0.001, 2) is not None
    # Block 1 is the reference and cannot drift from itself.
    assert drift_breach(0.0, 1) is None
    # And the band must be reachable in both directions on the scale it uses:
    # this is the check recall@8 would have failed at 0.90 + 0.19 = 1.09.
    assert BLOCK_1_REFERENCE_MRR + DIFFICULTY_BAND <= 1.0


def test_the_first_power_crossing_is_a_lower_bound_in_general() -> None:
    """The rule stated in `scripts/mcnemar.py`, checked beyond the one table.

    Pinning only theta = 0.8 would make the fix a fact about the numbers that
    happened to be wrong, and someone re-deriving from a different table would
    meet the same sawtooth with nothing to catch them. The direction of the
    error is the load-bearing part: the first crossing never overstates.
    """

    def first_crossing(target: float, theta: float) -> int:
        n = 1
        while power(n, theta) < target:
            n += 1
        return n

    gaps: list[int] = []
    for theta in (0.7, 0.75, 0.8, 0.9):
        for target in (0.5, 0.8):
            gap = min_discordant_for_power(target, theta) - first_crossing(target, theta)
            assert gap >= 0, f"first crossing overstated at theta={theta}, target={target}"
            gaps.append(gap)
    assert max(gaps) >= 3, "the sawtooth should be visible somewhere in this range"


def test_the_cumulative_total_includes_the_block_that_produced_it() -> None:
    """The bug: `cumulative` read artifacts from disk, and the current block's
    artifact is written *after* it runs.

    So the pooled total silently excluded the newest block — wrong by exactly
    the data the run existed to add, which is the one direction nobody checks,
    and it produced a plausible smaller number rather than an error. Pinned
    against the committed artifacts so a regression shows up as a mismatch
    rather than as a total that merely looks a bit low.
    """
    artifacts = sorted((REPO_ROOT / "evals").glob("interim-block-*.json"))
    if len(artifacts) < 2:
        pytest.skip("fewer than two interim artifacts")
    blocks = [json.loads(path.read_text(encoding="utf-8")) for path in artifacts]
    latest = blocks[-1]
    pooled = latest["cumulative"]

    assert pooled is not None, "a run with a prior block must pool"
    assert len(pooled["blocks"]) == len(blocks)
    assert pooled["n"] == sum(int(b["summary"]["n"]) for b in blocks)
    assert pooled["n_discordant"] == sum(int(b["summary"]["n_discordant"]) for b in blocks)
    assert pooled["per_block_n_discordant"][-1] == latest["summary"]["n_discordant"]


def test_the_pooled_artifacts_share_one_corpus_state() -> None:
    """Pooling across different corpus states would mix populations silently.

    `r` is a property of the questions *and* the corpus. A block measured
    against 358 chunks and one measured against 1041 are estimates of different
    quantities, and averaging them would produce a number that describes
    neither.
    """
    artifacts = sorted((REPO_ROOT / "evals").glob("interim-block-*.json"))
    if len(artifacts) < 2:
        pytest.skip("fewer than two interim artifacts")
    corpora = {json.loads(p.read_text(encoding="utf-8"))["corpus_chunks"] for p in artifacts}
    assert len(corpora) == 1, f"blocks measured against different corpora: {corpora}"
