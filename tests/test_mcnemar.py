"""The exact test's sizing arithmetic (`scripts/mcnemar.py`).

Kept after the comparison it sized was decided and its instruments removed
(SPEC-004 KD-15), because the sawtooth rule is general: **the first crossing is
a lower bound on the sustained power requirement, never an upper one.** Reading
it as the requirement was a live error in this repository — 12 and 20 were
published for power 0.5 and 0.8 before the dip at n = 14 and n = 22 was noticed
— and the next person to size a discrete test from a table will meet the same
shape.
"""

from scripts.mcnemar import (
    clopper_pearson,
    mcnemar_exact_two_sided,
    min_discordant_for_power,
    power,
)


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
