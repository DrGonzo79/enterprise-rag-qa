"""Baseline-artifact write guard (SPEC-004 AC-13, enforcing SPEC-003 AC-13).

A measured baseline records a corpus state that cannot be reconstructed once the
corpus changes. Producing one is therefore a deliberate act — `pytest
--write-baseline` — never a side effect of running the suite, and an artifact
that already exists under `evals/baselines/` is never rewritten at all.

The comparison lives here as pure functions so the guard is unit-testable
without staging a real violation; `conftest.py` wires it into the session hooks.
"""

import hashlib
from pathlib import Path

# Immutable, one file per measured corpus state (SPEC-003 AC-13).
BASELINE_DIR = Path("evals/baselines")
# The mutable convenience copy of the most recent run — rewritable, but only
# under the flag, since a plain `pytest` overwriting it is what prompted this.
MUTABLE_COPY = Path("evals/retrieval_baseline.json")


def snapshot(root: Path) -> dict[str, str]:
    """Repo-relative path -> sha256, for every guarded artifact that exists.

    A missing `evals/baselines/` is normal (the corpus ladder has not run yet)
    and snapshots as empty, so a file *appearing* there is still detected.
    """
    paths: list[Path] = []
    directory = root / BASELINE_DIR
    if directory.is_dir():
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    mutable = root / MUTABLE_COPY
    if mutable.is_file():
        paths.append(mutable)
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def violations(before: dict[str, str], after: dict[str, str], *, writes_allowed: bool) -> list[str]:
    """Guarded-artifact changes that must fail the run, worst first."""
    immutable_prefix = BASELINE_DIR.as_posix() + "/"
    problems: list[str] = []

    for path in sorted(set(before) | set(after)):
        was, now = before.get(path), after.get(path)
        if was == now:
            continue
        verb = "created" if was is None else "deleted" if now is None else "modified"

        if not writes_allowed:
            problems.append(f"{path} was {verb} by a test run without --write-baseline")
        elif was is not None and path.startswith(immutable_prefix):
            # Writing a *new* baseline is the whole point of the flag; changing
            # one that already existed destroys the before/after finding.
            problems.append(
                f"{path} was {verb} — baseline artifacts are immutable (SPEC-003 AC-13); "
                "write a new baseline-<chunk-count>-chunks.json instead"
            )

    return problems
