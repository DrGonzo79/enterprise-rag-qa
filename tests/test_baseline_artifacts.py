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
    "stop_condition",
}


def artifacts() -> list[Path]:
    return sorted(BASELINES_DIR.glob("baseline-*-chunks.json"))


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


def test_the_frozen_levers_agree_across_every_rung() -> None:
    """The comparison between rungs is only meaningful if the things the
    pre-registration froze actually stayed frozen. Two rungs measured with
    different chunker settings or a different question set are two different
    experiments, and the ladder would be comparing them as if they were one."""
    files = artifacts()
    if len(files) < 2:
        pytest.skip("only one rung measured so far; nothing to compare")

    loaded = [(p.name, json.loads(p.read_text(encoding="utf-8"))) for p in files]
    for lever in ("chunker_config", "question_set"):
        values = {name: json.dumps(data["corpus"][lever], sort_keys=True) for name, data in loaded}
        assert len(set(values.values())) == 1, (
            f"{lever} differs across rungs, so they are not comparable: {values}"
        )
    embedders = {name: data["embedder_identity"] for name, data in loaded}
    assert len(set(embedders.values())) == 1, f"embedder differs across rungs: {embedders}"
    ks = {name: data["k"] for name, data in loaded}
    assert len(set(ks.values())) == 1, f"k differs across rungs: {ks}"
