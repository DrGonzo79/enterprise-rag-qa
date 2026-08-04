"""How a gold label matches a section path (`scripts/section_match.py`).

**This rule exists because the bug was live and invisible.** Gold
`EU AI Act › Annex I` prefix-matched `Annex Ii`, `Annex Iii`, `Annex Iv` and
`Annex Ix`: five annexes scoring as one, four wrong answers counting as right,
and that question silently four times easier than every other in the set.
Nothing downstream could distinguish it from a question that was simply easy —
**it produced a plausible number instead of a visible failure**, which is the
class that survives review.

Pure: synthetic paths, no corpus, no database, so the rule is checked in CI
where the corpus does not exist.

**Mutation-verified** (CLAUDE.md rule 3), each applied to
`scripts/section_match.py` and reverted:
  - `matches_section` returns bare `section_path.startswith(prefix)` -> the
    Annex and Item cases FAIL.
  - `_breaks_at` returns `not rest` (exact match only) -> the descendant cases
    FAIL, which is the over-correction in the other direction: a gold label
    would then match only the chunk whose path equals it exactly, and every
    multi-chunk section would score as a permanent miss.
"""

from scripts.section_match import matches_section, straddles_a_component

# The five annexes that scored as one. Real paths from the ingested corpus.
ANNEXES = [
    "EU AI Act › Annex I — ANNEX I",
    "EU AI Act › Annex Ii — ANNEX II",
    "EU AI Act › Annex Iii — ANNEX III",
    "EU AI Act › Annex Iv — ANNEX IV",
    "EU AI Act › Annex Ix — ANNEX IX",
]


def test_the_annex_bug_in_full() -> None:
    """`Annex I` names one annex and must match exactly one of these five."""
    matched = [p for p in ANNEXES if matches_section("EU AI Act › Annex I", p)]
    assert matched == ["EU AI Act › Annex I — ANNEX I"]

    # And the four it must not match are reported as straddles, not silently dropped.
    straddled = [p for p in ANNEXES if straddles_a_component("EU AI Act › Annex I", p)]
    assert len(straddled) == 4


def test_a_label_matches_its_own_node_and_its_descendants() -> None:
    """Prefix matching is still the scoring rule — this narrows it, not replaces it.

    A gold label names a node in the section tree. Sections span several chunks
    and several sub-headings, and all of them are correct answers.
    """
    art112 = "EU AI Act › CHAPTER XIII › Article 112"
    assert matches_section(art112, art112)
    assert matches_section(
        "EU AI Act › CHAPTER XIII › Article 112",
        "EU AI Act › CHAPTER XIII › Article 112 — Evaluation and review",
    )
    assert matches_section(
        "NVIDIA 10-K FY2026 › Item 1C. Cybersecurity",
        "NVIDIA 10-K FY2026 › Item 1C. Cybersecurity › Risk management and strategy – Governance",
    )


def test_a_digit_boundary_is_not_a_break_either() -> None:
    """`Item 1` must not swallow `Item 15`, and `Article 5` must not swallow `Article 55`.

    The alphabetic case is what was found; the numeric case is the same bug and
    is the one this corpus is denser in — there are 113 articles and a dozen
    10-K items whose labels nest by digit.
    """
    art5 = "EU AI Act › CHAPTER II › Article 5"
    assert not matches_section(
        "NVIDIA 10-K FY2026 › Item 1", "NVIDIA 10-K FY2026 › Item 15. Exhibits"
    )
    assert not matches_section(art5, "EU AI Act › CHAPTER II › Article 55")
    assert matches_section(art5, f"{art5} — Prohibited AI practices")


def test_an_underscore_does_not_break_a_component() -> None:
    """`_` is a word character here, and it is also a SQL LIKE wildcard.

    Both facts point the same way: a label ending before an underscore has not
    ended at a component break, and the pre-flight no longer uses LIKE at all so
    the wildcard cannot widen the check that exists to catch widening.
    """
    assert not matches_section("doc › part_a", "doc › part_a_continued")


def test_straddles_is_the_complement_among_prefix_matches() -> None:
    """Exactly the set of over-matches a raw `startswith` scorer would count.

    Stated as a property rather than as examples so that a future separator
    cannot satisfy the examples while reopening the hole.
    """
    prefix = "EU AI Act › Annex I"
    for path in [*ANNEXES, "NVIDIA 10-K FY2026 › Item 1", "unrelated"]:
        if path.startswith(prefix):
            assert matches_section(prefix, path) != straddles_a_component(prefix, path)
        else:
            assert not matches_section(prefix, path)
            assert not straddles_a_component(prefix, path)
