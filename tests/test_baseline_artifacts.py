"""Every committed rung artifact describes its own corpus, consistently.

SPEC-003 AC-13. Pure: reads the committed files, needs no corpus, no database
and no API key, so the check runs in CI where the artifacts are never written.

The failure this guards is quiet and unrecoverable. A rung artifact is the only
record of a corpus state that ceases to exist the moment the next rung is
ingested; an artifact whose recorded chunk count disagrees with its own
per-document breakdown is a record of *nothing*, and there is no way to tell
afterwards which of the two numbers was the real one.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINES_DIR = REPO_ROOT / "evals" / "baselines"

REQUIRED_TOP_LEVEL = {
    "rung",
    "measured_at",
    "git_sha",
    "embedder_identity",
    "k",
    "corpus",
    "preregistration",
    "pairs",
}

# The artifact shape changed with the pre-registration, and pretending otherwise
# would mean either loosening the check to the older shape's fields or rewriting
# a superseded record. SPEC-007 KD-12 amendment 1 replaced a single
# `stop_condition` — which conflated a corpus gate with a result — by two
# separate blocks, plus an explicit deviations list.
SHAPE_BY_PREREGISTRATION = {
    "prereg-1": {"stop_condition"},
    "prereg-2": {"corpus_adequacy", "comparison", "deviations"},
}


def artifacts() -> list[Path]:
    # `baseline-*-chunks.json` was too narrow and silently skipped every artifact
    # carrying a pre-registration suffix -- i.e. exactly the ones a schema change
    # makes worth checking. A guard that stops seeing files as the naming grows is
    # worse than no guard, because the count of passing checks still goes up.
    return sorted(BASELINES_DIR.glob("baseline-*.json"))


def test_at_least_the_pre_expansion_artifact_exists() -> None:
    """SPEC-003's sequencing: the pre-expansion baseline is committed *before*
    the first fetch, because fetching anything destroys the only "before" the
    ladder will ever have."""
    assert artifacts(), (
        "no rung artifact under evals/baselines/. Run the real-corpus tier with "
        "--write-baseline before fetching any rung (SPEC-003 AC-13)."
    )


@pytest.mark.parametrize("path", artifacts(), ids=lambda p: p.name)
def test_each_artifact_is_self_describing_and_internally_consistent(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))

    missing = REQUIRED_TOP_LEVEL - set(data)
    assert not missing, f"{path.name} is missing {sorted(missing)}"

    prereg = data["preregistration"].get("preregistration_id", "prereg-1")
    assert prereg in SHAPE_BY_PREREGISTRATION, f"{path.name} names unknown {prereg}"
    shape_missing = SHAPE_BY_PREREGISTRATION[prereg] - set(data)
    assert not shape_missing, f"{path.name} is a {prereg} artifact missing {sorted(shape_missing)}"

    if prereg != "prereg-1":
        # A run against a set that is not the pre-registered size is under-powered
        # relative to the published table, and saying so is the whole mechanism
        # that separates a declared deviation from a silent substitution.
        actual = data["corpus"]["question_set"]["count"]
        expected = data["preregistration"]["retrieval_set_size"]
        if actual != expected:
            assert data["deviations"], (
                f"{path.name} ran {actual} questions against a pre-registered {expected} "
                f"and recorded no deviation"
            )

    corpus = data["corpus"]
    per_document = corpus["per_document"]
    assert per_document, f"{path.name} records no per-document breakdown"

    summed = sum(int(doc["chunks"]) for doc in per_document)
    assert corpus["chunk_count"] == summed, (
        f"{path.name} records chunk_count={corpus['chunk_count']} but its per-document "
        f"counts sum to {summed} — one of the two is wrong and nothing can say which"
    )
    assert corpus["document_count"] == len(per_document)

    # The filename carries the chunk count, and it is what a reader greps for.
    from_name = int(path.stem.split("-")[1])
    assert from_name == corpus["chunk_count"], (
        f"{path.name} is named for {from_name} chunks but records {corpus['chunk_count']}"
    )

    # The levers the pre-registration holds fixed have to be *recorded*, or
    # "held fixed" is an assertion about a run nobody can check afterwards.
    assert data["k"] == data["preregistration"]["k"]
    assert corpus["chunker_config"], f"{path.name} records no chunker config"
    assert corpus["question_set"]["sha256"], f"{path.name} records no question-set hash"
    assert data["embedder_identity"]


def test_the_frozen_levers_agree_within_each_preregistration() -> None:
    """The comparison between rungs is only meaningful if the things the
    pre-registration froze actually stayed frozen. Two rungs measured with
    different chunker settings or a different question set are two different
    experiments, and the ladder would be comparing them as if they were one.

    **Grouped by pre-registration, not across all artifacts.** The question set
    is frozen *within* a pre-registration and may change *between* them — that
    distinction is the fix in SPEC-007 KD-12 amendment 1, since freezing it
    forever is what made prereg-1's threshold unreachable. Comparing a prereg-1
    rung to a prereg-2 rung is the mistake this guard exists to prevent, so it
    never compares them at all.
    """
    groups: dict[str, list[tuple[str, dict[str, object]]]] = {}
    for path in artifacts():
        data = json.loads(path.read_text(encoding="utf-8"))
        prereg = data["preregistration"].get("preregistration_id", "prereg-1")
        groups.setdefault(prereg, []).append((path.name, data))

    compared = 0
    for prereg, loaded in groups.items():
        if len(loaded) < 2:
            continue
        compared += 1
        for lever in ("chunker_config", "question_set"):
            values = {
                name: json.dumps(data["corpus"][lever], sort_keys=True)  # type: ignore[index]
                for name, data in loaded
            }
            assert len(set(values.values())) == 1, (
                f"{lever} differs across {prereg} rungs, so they are not comparable: {values}"
            )
        for scalar in ("embedder_identity", "k"):
            values = {name: str(data[scalar]) for name, data in loaded}
            assert len(set(values.values())) == 1, (
                f"{scalar} differs across {prereg} rungs: {values}"
            )

    if not compared:
        pytest.skip("no pre-registration has two rungs yet; nothing to compare")
