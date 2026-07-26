"""Baseline write-guard tests (SPEC-004 AC-13).

Pure — no corpus, no database, no API key. The guard's whole job is to fail a
run, so it is tested against staged snapshots rather than by actually
overwriting a committed artifact.
"""

import json
from pathlib import Path

from baseline_guard import BASELINE_DIR, MUTABLE_COPY, snapshot, violations

REPO_ROOT = Path(__file__).resolve().parent.parent


def _stage(root: Path, relative: str, payload: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


# --- snapshot -----------------------------------------------------------------


def test_missing_baseline_dir_snapshots_empty(tmp_path: Path) -> None:
    """The ladder has not run yet, so `evals/baselines/` does not exist — the
    guard must still notice a file appearing there."""
    assert snapshot(tmp_path) == {}

    _stage(tmp_path, "evals/baselines/baseline-358-chunks.json", "{}")
    after = snapshot(tmp_path)
    assert list(after) == ["evals/baselines/baseline-358-chunks.json"]
    assert violations({}, after, writes_allowed=False)


def test_snapshot_covers_both_guarded_paths(tmp_path: Path) -> None:
    _stage(tmp_path, str(BASELINE_DIR / "baseline-358-chunks.json"), '{"cases": 26}')
    _stage(tmp_path, str(MUTABLE_COPY), '{"cases": 26}')
    assert set(snapshot(tmp_path)) == {
        "evals/baselines/baseline-358-chunks.json",
        "evals/retrieval_baseline.json",
    }


def test_snapshot_tracks_content_not_mtime(tmp_path: Path) -> None:
    """Identical bytes rewritten are not a change; one byte different is."""
    path = _stage(tmp_path, str(MUTABLE_COPY), '{"recall_at_8": 1.0}')
    before = snapshot(tmp_path)
    path.write_text('{"recall_at_8": 1.0}', encoding="utf-8")
    assert snapshot(tmp_path) == before

    path.write_text('{"recall_at_8": 0.94}', encoding="utf-8")
    assert snapshot(tmp_path) != before


# --- violations ---------------------------------------------------------------


def test_plain_run_may_not_touch_any_guarded_artifact() -> None:
    """The failure that prompted this guard: a routine `pytest` rewrote the
    baseline. Every kind of change counts, including creation and deletion."""
    before = {"evals/retrieval_baseline.json": "aaa", "evals/baselines/b-358.json": "bbb"}

    def plain(after: dict[str, str]) -> list[str]:
        return violations(before, after, writes_allowed=False)

    modified = plain({**before, "evals/retrieval_baseline.json": "zzz"})
    assert len(modified) == 1 and "modified" in modified[0]
    assert "--write-baseline" in modified[0]

    created = plain({**before, "evals/baselines/b-1004.json": "ccc"})
    assert len(created) == 1 and "created" in created[0]

    deleted = plain({"evals/retrieval_baseline.json": "aaa"})
    assert len(deleted) == 1 and "deleted" in deleted[0]


def test_unchanged_run_is_silent() -> None:
    state = {"evals/retrieval_baseline.json": "aaa", "evals/baselines/b-358.json": "bbb"}
    assert violations(state, dict(state), writes_allowed=False) == []
    assert violations(state, dict(state), writes_allowed=True) == []


def test_flag_permits_a_new_baseline_but_never_rewrites_an_old_one() -> None:
    """SPEC-003 AC-13: each corpus state gets its own file. Adding the next
    rung's artifact is the point; overwriting the previous rung's destroys the
    before/after comparison that the ladder exists to produce."""
    before = {"evals/baselines/baseline-358-chunks.json": "aaa"}

    added = {**before, "evals/baselines/baseline-1004-chunks.json": "bbb"}
    assert violations(before, added, writes_allowed=True) == []

    overwritten = {"evals/baselines/baseline-358-chunks.json": "zzz"}
    problems = violations(before, overwritten, writes_allowed=True)
    assert len(problems) == 1
    assert "immutable" in problems[0]
    assert "SPEC-003 AC-13" in problems[0]

    removed = violations(before, {}, writes_allowed=True)
    assert len(removed) == 1 and "immutable" in removed[0]


def test_flag_permits_rewriting_the_mutable_copy() -> None:
    """`evals/retrieval_baseline.json` is explicitly the most-recent-run copy,
    not a record of any corpus state — rewritable, but only under the flag."""
    before = {"evals/retrieval_baseline.json": "aaa"}
    assert violations(before, {"evals/retrieval_baseline.json": "zzz"}, writes_allowed=True) == []


# --- the committed artifact ---------------------------------------------------


def test_committed_baseline_is_valid_json_and_self_describing() -> None:
    """Cheap integrity check on the artifact the guard protects."""
    baseline = json.loads((REPO_ROOT / MUTABLE_COPY).read_text(encoding="utf-8"))
    assert baseline["cases"] == len(baseline["per_case"])
    assert baseline["measured_on"]  # names the corpus state it describes
    assert baseline["k"] == 8
