"""The exact conditional test and its sizing arithmetic (SPEC-007 Key decision 12).

**One copy, imported by both the pilot and the blinded interim.** Two copies of
the floor constant is precisely the drift this project's claim-sweeps keep
finding, and the floor is the number every sizing decision hangs off.

`mcnemar_exact_two_sided` takes the split and is called by the pilot only. The
interim (`scripts/interim_r.py`) never calls it — see SPEC-007 AC-17. Nothing
here is what makes the interim blinded; that property belongs to what the
interim *emits*, not to what it can import.
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
