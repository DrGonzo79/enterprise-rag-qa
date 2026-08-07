"""The exact conditional test and its sizing arithmetic (SPEC-007 Key decision 12).

**One copy, imported by both the pilot and the blinded interim.** Two copies of
the floor constant is precisely the drift this project's claim-sweeps keep
finding, and the floor is the number every sizing decision hangs off.

`mcnemar_exact_two_sided` takes the split and is called by the pilot only. The
interim (`scripts/interim_r.py`) never calls it — see SPEC-007 AC-18. Nothing
here is what makes the interim blinded; that property belongs to what the
interim *emits*, not to what it can import.

--------------------------------------------------------------------------
**RULE: the first crossing is the wrong reading of any discrete power curve.**

This is stated generally because it is general, and because the specific
instance of it — reading 12 and 20 off a table for this test at θ = 0.8 — was
published here before it was caught. Anyone re-deriving a sample size from a
different table, a different alpha, or a different test will meet the same shape.

**Power is not monotone in n for a discrete test.** The rejection region can
only change in whole observations, so as n grows the critical value jumps and
the attained size drops; power follows a **sawtooth**, rising within a step and
falling at each jump. At θ = 0.8 here: n = 12 gives 0.558, n = 13 gives 0.502,
n = 14 gives **0.448**. A set sized at 12 for "power 0.5" has power 0.448 if it
happens to collect 14 discordant pairs.

**The consequence, which is the part to remember: the first crossing is a lower
bound on the sustained requirement, never an upper one.** Measured across
θ ∈ {0.7, 0.75, 0.8, 0.9} and targets {0.5, 0.8}, the first crossing understates
the sustained requirement by 0 to 8 discordant pairs and **never overstates it**.
So the error has a direction — reading the first crossing always buys *less*
power than the number advertises, and it does so silently, because the
arithmetic that produced it is correct as far as it goes.

**Take the sustained crossing**: the smallest n whose power reaches the target
*and stays at or above it*. That is what `min_discordant_for_power` returns, and
the sawtooth itself is pinned by test rather than described only here.
--------------------------------------------------------------------------
"""

import math

# Derived from the exact binomial and alpha alone, not chosen: the most extreme
# split at n discordant pairs gives p = 2 * 2**-n, which is >= 0.05 for n <= 5.
MIN_DISCORDANT_FOR_ANY_REJECTION = 6


def mcnemar_exact_two_sided(b: int, c: int) -> float:
    """Exact two-sided p, conditional on the n = b + c discordant pairs.

    Under H0 the direction of each discordant pair is a fair coin, so b is
    Binomial(n, 1/2) and the p-value doubles the smaller tail.
    """
    n = b + c
    if n == 0:
        return 1.0
    lower = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * lower)


def reject_set(n: int, alpha: float = 0.05) -> set[int]:
    """The values of b at which the test rejects, given n discordant pairs."""
    return {b for b in range(n + 1) if mcnemar_exact_two_sided(b, n - b) < alpha}


def power(n: int, theta: float, alpha: float = 0.05) -> float:
    """P(reject | n discordant pairs, theta = P(hybrid wins | discordant)).

    Computed over the exact rejection set rather than from a normal
    approximation, because at these n the approximation is the part that would
    be wrong.
    """
    return sum(math.comb(n, b) * theta**b * (1 - theta) ** (n - b) for b in reject_set(n, alpha))


def min_discordant_for_power(
    target: float, theta: float, alpha: float = 0.05, limit: int = 400
) -> int:
    """Smallest n whose power reaches `target` **and stays there** for all larger n.

    The "and stays there" is not pedantry: power is *not* monotone in n for a
    discrete test — adding one discordant pair can move the critical value and
    lose power — so the first crossing is a number that a slightly larger set
    can fall back below. Sizing off the first crossing would buy a promise the
    next question could break.
    """
    for n in range(1, limit + 1):
        if all(power(m, theta, alpha) >= target for m in range(n, min(n + 20, limit) + 1)):
            return n
    raise ValueError(f"power {target} not reached at theta={theta} below n={limit}")


def _normal_quantile(p: float) -> float:
    """Inverse standard normal CDF, by bisection on `math.erf`.

    Bisection rather than a hard-coded 1.96 so `alpha` is a real parameter: a
    constant here would be correct for the default and silently wrong for every
    other alpha a caller passes.
    """
    low, high = -10.0, 10.0
    for _ in range(200):
        mid = (low + high) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def wilson(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Score interval for a single binomial proportion.

    Wilson rather than Clopper-Pearson here because `newcombe_paired_difference`
    is built out of these and Newcombe's method is defined on the score interval;
    substituting the exact interval would change the construction, not just
    tighten it.
    """
    if n == 0:
        return (0.0, 1.0)
    z = _normal_quantile(1 - alpha / 2)
    p = x / n
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half_width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - half_width) / denominator, (centre + half_width) / denominator)


def newcombe_paired_difference(
    both: int, first_only: int, second_only: int, neither: int, alpha: float = 0.05
) -> tuple[float, float]:
    """Interval for p1 - p2 on PAIRED binary outcomes (Newcombe 1998, method 10).

    **This is the quantity a reader actually carries away** — "0.9167 against
    0.775, so 14 points" — and it is a third parameter, distinct from both of the
    intervals already in the report:

    - it is **not** either arm's Wilson interval, which is about one arm alone;
    - it is **not** the discordance split's interval, which is about
      P(one arm wins | the pair is discordant) — a conditional probability on a
      subset, not a difference on the whole set.

    Reporting a difference under either of those is the specific way a figure
    misleads while every number in it is true (SPEC-007 Key decision 1).

    **Construction.** Square-and-add of the two arms' Wilson intervals, with the
    paired correlation estimated from the table:

        phi = (both*neither - first_only*second_only) / sqrt(row and column totals)

    and phi enters with a factor of -2, so a positive correlation *narrows* the
    interval. That narrowing is what pairing buys, and it is why the unpaired
    interval on the same four counts is the wrong one rather than a conservative
    one: it is wider, but it is answering a question nobody asked.
    """
    n = both + first_only + second_only + neither
    if n == 0:
        return (-1.0, 1.0)
    p1 = (both + first_only) / n
    p2 = (both + second_only) / n
    lower1, upper1 = wilson(both + first_only, n, alpha)
    lower2, upper2 = wilson(both + second_only, n, alpha)

    # Zero denominator means a margin is degenerate (an arm was right on every
    # question, or wrong on every one). The correlation is then undefined rather
    # than zero, and 0 is the choice that does not narrow anything.
    product = (
        (both + first_only)
        * (second_only + neither)
        * (both + second_only)
        * (first_only + neither)
    )
    phi = (both * neither - first_only * second_only) / math.sqrt(product) if product > 0 else 0.0

    difference = p1 - p2
    low = difference - math.sqrt(
        (p1 - lower1) ** 2 - 2 * phi * (p1 - lower1) * (upper2 - p2) + (upper2 - p2) ** 2
    )
    high = difference + math.sqrt(
        (upper1 - p1) ** 2 - 2 * phi * (upper1 - p1) * (p2 - lower2) + (p2 - lower2) ** 2
    )
    return (low, high)


def clopper_pearson(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact (conservative) two-sided interval for a binomial rate.

    Bisection on the exact binomial tails rather than a beta quantile, so this
    stays dependency-free; the tails are the definition, not an approximation
    of it.
    """
    if n == 0:
        return (0.0, 1.0)

    def upper_tail(p: float, k: int) -> float:
        return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))

    def lower_tail(p: float, k: int) -> float:
        return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))

    def solve(f: object, target: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if f(mid) < target:  # type: ignore[operator]
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    low = 0.0 if x == 0 else solve(lambda p: upper_tail(p, x), alpha / 2)
    high = 1.0 if x == n else 1.0 - solve(lambda p: lower_tail(1 - p, x), alpha / 2)
    return (low, high)
