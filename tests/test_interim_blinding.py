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
from scripts.interim_r import COMMITTED_BLOCK_MIX, case_record, check_mix, sizing, summarise
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


def test_the_blocks_compose_to_the_committed_150() -> None:
    """105/23/22 exactly, which is what the 5/4 alternation is for."""
    totals: dict[str, int] = {}
    for mix in COMMITTED_BLOCK_MIX.values():
        assert sum(mix.values()) == 30
        for shape, count in mix.items():
            totals[shape] = totals.get(shape, 0) + count
    assert totals == {"natural-language": 105, "citation-anchored": 23, "cross-section": 22}
    assert sum(totals.values()) == 150


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
