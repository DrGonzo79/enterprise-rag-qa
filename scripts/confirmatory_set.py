"""The confirmatory set's frozen pre-registration (SPEC-007 KD-12).

The instruments that produced the comparison — `run_pilot`, `interim_r`,
`confirmatory`, `screen_fallback_gate` — were removed with the branch they
measured (SPEC-004 KD-17); every artifact in `evals/` records the `git_sha` at
which its instrument ran, so each is recoverable from history.

**What survives here is the pre-registration itself**, because the set is
permanently closed (KD-12 amendment 8) and its composition is a fact about the
artifact rather than about any script. The tests that guard the set read it.
"""

# 15% of 30 is 4.5, so the two minority shapes alternate across blocks. Six
# blocks of 30 plus one of 20 compose to the cap of 200 at exactly 140/30/30.
# The set stopped at block 4, N = 120, at exactly 84/18/18 — 70/15/15 with no
# residue.
COMMITTED_BLOCK_MIX: dict[int, dict[str, int]] = {
    1: {"natural-language": 21, "citation-anchored": 5, "cross-section": 4},
    2: {"natural-language": 21, "citation-anchored": 4, "cross-section": 5},
    3: {"natural-language": 21, "citation-anchored": 5, "cross-section": 4},
    4: {"natural-language": 21, "citation-anchored": 4, "cross-section": 5},
    5: {"natural-language": 21, "citation-anchored": 5, "cross-section": 4},
    6: {"natural-language": 21, "citation-anchored": 4, "cross-section": 5},
    7: {"natural-language": 14, "citation-anchored": 3, "cross-section": 3},
}

TARGET_DISCORDANT = 23
N_CAP = 200
