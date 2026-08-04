"""The confirmatory retrieval set is well-formed and matches its pre-registration.

SPEC-007 KD-12 amendments 4 and 5. Pure: reads the committed file, needs no
corpus, no database and no API key, so it runs in CI where the corpus does not
exist.

**What this is actually guarding.** The set is authored across sessions, one
30-question block at a time, against a mix that was committed before any of it
existed. The failure mode is drift — a block that quietly ends up 24/3/3
because natural-language questions are the easiest to write — and drift is
invisible in a JSONL file that a human reads a line at a time. `r` estimated on
a block that is not the mix is `r` for a population the set does not contain.

The gold labels themselves cannot be checked here: whether
`expected_section_prefix` names a section that exists is a fact about the
ingested corpus, and it is checked by the interim run, which refuses to score a
prefix that resolves to nothing.
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from scripts.interim_r import COMMITTED_BLOCK_MIX

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIRMATORY = REPO_ROOT / "evals" / "retrieval_confirmatory.jsonl"
EXCLUDED_SETS = ("retrieval_pilot.jsonl", "retrieval_pilot2.jsonl", "retrieval_smoke.jsonl")

REQUIRED = {"id", "block", "shape", "document", "question", "expected_section_prefix"}
DOCUMENT_ROOTS = {
    "eu-ai-act": "EU AI Act › ",
    "nvidia-10k": "NVIDIA 10-K FY2026 › ",
    "nist-ai-rmf": "NIST AI RMF 1.0 › ",
}


def cases() -> list[dict[str, object]]:
    if not CONFIRMATORY.exists():
        pytest.skip("confirmatory set not authored yet")
    lines = CONFIRMATORY.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def test_every_case_carries_the_required_fields() -> None:
    for case in cases():
        assert set(case) >= REQUIRED, f"{case.get('id')} is missing {REQUIRED - set(case)}"
        assert str(case["question"]).strip(), case["id"]
        assert str(case["label_reason"]).strip(), case["id"]


def test_ids_are_unique() -> None:
    ids = [c["id"] for c in cases()]
    assert len(ids) == len(set(ids))


def test_each_block_matches_its_committed_mix() -> None:
    """The pre-registration is enforced per block, not only in aggregate.

    Aggregate-only would let block 1 be all natural-language and block 5
    compensate, which is exactly the thing the interim cannot survive: the
    interim reads block 1 alone.
    """
    by_block: dict[int, Counter[str]] = {}
    for case in cases():
        by_block.setdefault(int(str(case["block"])), Counter())[str(case["shape"])] += 1
    assert by_block, "no blocks"
    for block, actual in sorted(by_block.items()):
        expected = COMMITTED_BLOCK_MIX[block]
        assert dict(actual) == expected, f"block {block}: {dict(actual)} != {expected}"


def test_prefixes_are_rooted_in_the_document_the_case_names() -> None:
    """A prefix that disagrees with its own `document` field is a mislabel.

    Cheap to check and it catches the copy-paste that produces a gold label
    pointing into the wrong document entirely — which would score as a
    permanent miss for both arms and silently depress `recall` for reasons that
    have nothing to do with retrieval.
    """
    for case in cases():
        root = DOCUMENT_ROOTS[str(case["document"])]
        assert str(case["expected_section_prefix"]).startswith(root), case["id"]
        if "also_contains" in case:
            assert str(case["also_contains"]).startswith(root), case["id"]


def test_cross_section_cases_name_both_halves() -> None:
    """`also_contains` is what makes a span question checkable as a span.

    Without it there is no record that the question was authored to span, and a
    cross-section case is indistinguishable afterwards from a natural-language
    one whose label happened to be hard.
    """
    for case in cases():
        if case["shape"] == "cross-section":
            assert "also_contains" in case, case["id"]
            assert case["also_contains"] != case["expected_section_prefix"], case["id"]
        else:
            assert "also_contains" not in case, case["id"]


def test_no_case_is_shared_with_the_pilots_or_the_smoke_set() -> None:
    """The pilots are excluded by pre-registration; the smoke set by amendment 5.

    Checked on both the id and the question text, because an id collision is the
    accident nobody makes twice and a copied question is the one that would
    survive review.
    """
    confirmatory = cases()
    ours_ids = {c["id"] for c in confirmatory}
    ours_questions = {str(c["question"]).strip().lower() for c in confirmatory}
    for name in EXCLUDED_SETS:
        path = REPO_ROOT / "evals" / name
        other = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        assert not ours_ids & {c["id"] for c in other}, name
        assert not ours_questions & {str(c["question"]).strip().lower() for c in other}, name


def test_natural_language_questions_carry_no_citation() -> None:
    """The shape is a claim about the question, and it has to be true of it.

    A "natural-language" question containing "Article 6" is a citation-anchored
    question with the wrong label, and the mix is only meaningful if the labels
    describe the text.
    """
    markers = ("article ", "annex ", "item 1", "item 5", "item 7", "item 9", "recital ")
    for case in cases():
        if case["shape"] != "natural-language":
            continue
        lowered = str(case["question"]).lower()
        found = [m for m in markers if m in lowered]
        assert not found, f"{case['id']} is labelled natural-language but cites {found}"
