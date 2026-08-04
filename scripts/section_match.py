"""How a gold label is compared to a section path. One copy, used by every scorer.

**The bug this exists for was live and produced a plausible number rather than a
visible failure**, which is the kind that survives. Gold `EU AI Act > Annex I`
matched `Annex Ii`, `Annex Iii`, `Annex Iv` and `Annex Ix` — five annexes
scoring as one, four wrong answers counting as right, and that question silently
four times easier than every other in the set. Nothing downstream could have
told it apart from a question that was simply easy.

**Prefix matching is the right scoring rule; raw `startswith` is the wrong
implementation of it.** A gold label names a *node* in the section tree and
should match that node and its descendants — so the match has to end where a
path component ends, never in the middle of a word.

Kept in one module because the failure mode is a scorer that disagrees with the
pre-flight check that was supposed to protect it. Three scorers used their own
`startswith` before this existed (`scripts/run_pilot.py`,
`scripts/probe_corpus.py`, `tests/test_retrieval_quality.py`), and only the
confirmatory set had a guard in front of it.
"""

# A component break is anything that is not a word character. In this corpus the
# separators are " › ", " — " and " ", but the rule is stated as the complement
# so that a new separator introduced by a future loader cannot quietly reopen
# the hole.
_WORD = "_"


def _breaks_at(rest: str) -> bool:
    return not rest or not (rest[0].isalnum() or rest[0] == _WORD)


def matches_section(prefix: str, section_path: str) -> bool:
    """True when `section_path` is the node `prefix` names, or a descendant of it."""
    return section_path.startswith(prefix) and _breaks_at(section_path[len(prefix) :])


def straddles_a_component(prefix: str, section_path: str) -> bool:
    """True when `prefix` matches `section_path` mid-word rather than at a break.

    The complement of `matches_section` *among paths that `startswith` accepts* —
    which is exactly the set of over-matches a raw prefix comparison would score
    as hits.
    """
    return section_path.startswith(prefix) and not matches_section(prefix, section_path)
