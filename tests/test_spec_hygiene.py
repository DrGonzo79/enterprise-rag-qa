"""Spec bookkeeping that was being caught by people looking rather than by anything failing.

Two properties, both added 2026-08-05 after each was violated once and found by
inspection:

1. **A spec whose acceptance criteria the test suite cites may not read `Draft`.**
   SPEC-007 was ratified item by item on 2026-08-02 and its Status line said
   "Draft — awaiting review" until 2026-08-05, across eleven commits that
   implemented it. Rule 4 puts the Status move with the owner and required
   nothing of the owner, so the gap had no mechanism to close it.
2. **KD and AC identifiers are unique and contiguous within a spec.** `AC-17`
   was used twice in SPEC-007, and SPEC-004's Key decisions jumped 14 → 17.
   Both were introduced by the same author within three days, which is a rate
   rather than an accident.

Pure: reads `specs/` and `tests/`, no corpus, no database, no API key.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS = sorted((REPO_ROOT / "specs").glob("SPEC-*.md"))

# `1. **Decision.**` at the left margin — the numbered-list form every spec uses
# for Key decisions. Sub-decisions (`7a.`) are deliberately not matched: they
# are amendments hanging off a decision, not decisions in the sequence.
KD_PATTERN = re.compile(r"^(\d+)\. \*\*", re.MULTILINE)
# `- **AC-3 (…)**` — the definition form, as distinct from a mention in prose.
# `- **AC-3 (…)**` — the DEFINITION form. The parenthesis is load-bearing: a
# first pass matched `AC-\d+ ` and flagged SPEC-003's `- **AC-2 restated per
# document**`, which is an amendment TO AC-2 rather than a second definition of
# it. A checker that cannot tell those apart would have to be silenced, and a
# silenced checker is the thing this file exists to avoid.
AC_DEFINITION = re.compile(r"^- \*\*AC-(\d+) \(", re.MULTILINE)
STATUS = re.compile(r"^\*\*Status:\*\*\s*(.+)$", re.MULTILINE)

# Draft specs a test may name without claiming to enforce them. One entry, and
# it has to earn its place: `test_api_conditions.py` names SPEC-009 AC-2 to say
# what the exported contract is an INPUT to, while asserting SPEC-006's half.
POINTERS_TO_DRAFT_SPECS = {
    "SPEC-009": "named as the consumer of SPEC-006's exported contract; not enforced here",
}


def spec_id(path: Path) -> str:
    return path.name.split("-")[0] + "-" + path.name.split("-")[1]


def status_of(path: Path) -> str:
    match = STATUS.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def test_every_spec_declares_a_status() -> None:
    for path in SPECS:
        assert status_of(path), f"{path.name} has no Status line"


def test_no_draft_spec_is_enforced_by_the_test_suite() -> None:
    """Citing a spec's decisions in a test *is* the claim that it is enforced.

    **The first version of this test could not have caught the incident it was
    written for**, and that is worth keeping in the file rather than in a commit
    message. It looked for `SPEC-NNN AC-N` and skipped comment lines. SPEC-007's
    tests cite it as `SPEC-007 KD-12`, in docstrings — so the trigger never
    fired, the test passed, and reverting the Status line to Draft left it
    green. Rule 3's family, in a test written to enforce rule 4.

    So the trigger is now **any** structured citation — `AC-N`, `KD-N`, or
    `Key decision N` — anywhere in the test suite, comments included. That is
    deliberately broad: a genuine pointer to a Draft spec is fine and costs one
    allowlist entry with a reason, which is cheaper than a checker that misses
    the case it exists for.
    """
    citation = re.compile(r"(SPEC-\d+)[ \u2014-]*\s*(?:AC-\d+|KD-\d+|Key decision \d+)")
    cited: dict[str, set[str]] = {}
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        if path.name == "test_spec_hygiene.py":
            continue
        for match in citation.finditer(path.read_text(encoding="utf-8")):
            cited.setdefault(match.group(1), set()).add(path.name)

    offenders = {
        spec_id(path): sorted(cited[spec_id(path)])
        for path in SPECS
        if spec_id(path) in cited
        and status_of(path).lower().startswith("draft")
        and spec_id(path) not in POINTERS_TO_DRAFT_SPECS
    }
    assert not offenders, (
        f"Draft specs enforced by tests: {offenders}. Rule 4's completion clause — a "
        "ratification that has not moved the Status line has not happened, and only "
        "the repository owner can move it. If the citation is a pointer rather than "
        "an enforcement, add it to POINTERS_TO_DRAFT_SPECS with a reason."
    )


def test_key_decision_numbers_are_unique_and_contiguous() -> None:
    """A gap is a decision nobody can cite and nobody notices is missing.

    SPEC-004 jumped 14 → 17 when two decisions were added without checking the
    sequence, which meant a cross-reference to "KD-15" would have pointed at
    nothing for as long as anybody cared to look.
    """
    for path in SPECS:
        numbers = [int(n) for n in KD_PATTERN.findall(path.read_text(encoding="utf-8"))]
        # The Purpose section also uses a numbered list; Key decisions are the
        # longest such run, so take the maximal contiguous sequence from 1.
        seen = sorted(set(numbers))
        if not seen:
            continue
        assert seen == list(range(1, max(seen) + 1)), (
            f"{path.name} Key decision numbers are not contiguous: "
            f"missing {sorted(set(range(1, max(seen) + 1)) - set(seen))}"
        )


def test_acceptance_criterion_numbers_are_unique_and_contiguous() -> None:
    """`AC-17` was defined twice in SPEC-007 and stood for three days.

    Every test and commit message referring to "AC-17" in the newer sense was
    pointing at a number that already meant something else.
    """
    for path in SPECS:
        numbers = [int(n) for n in AC_DEFINITION.findall(path.read_text(encoding="utf-8"))]
        if not numbers:
            continue
        duplicates = {n for n in numbers if numbers.count(n) > 1}
        assert not duplicates, f"{path.name} defines AC-{sorted(duplicates)} more than once"
        seen = sorted(set(numbers))
        assert seen == list(range(1, max(seen) + 1)), (
            f"{path.name} acceptance criteria are not contiguous: "
            f"missing {sorted(set(range(1, max(seen) + 1)) - set(seen))}"
        )
