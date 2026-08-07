"""Invariants of the scoring path that a sweep can establish once and a test keeps.

Three properties, each of which was checked by hand and then found to be the
kind of thing that would silently regress:

1. **Section paths are compared only through `scripts/section_match.py`.** Five
   sites had their own `startswith` and one (`tests/baseline_guard.py`) had
   independently arrived at the right rule — which means the codebase lacked a
   shared primitive, not that it had five bugs. A sixth site now fails here.
2. **The gold-label pre-flight runs before any embedding is billed.** Refusing
   a bad label at scoring time would mean paying for the block first.
3. **Every committed eval artifact records an exact query plan.** The HNSW index
   is not in any plan today; if that changes, some fraction of every `recall@8`
   miss becomes an index artifact rather than a retrieval failure, and
   `recall@8` is the pre-registered primary metric.

Pure: AST and committed artifacts, no corpus, no database, no API key.
"""

import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("src", "scripts", "tests")

# The rule's own home, and the only file allowed to implement it.
RULE_MODULE = "scripts/section_match.py"

# Prefix comparisons that are deliberate and are NOT section-path comparisons.
# Each needs a reason; adding one without a reason is the failure this guards.
ALLOWED_PREFIX_COMPARISONS = {
    # A filesystem directory prefix, and it appends "/" before comparing — the
    # same component-break rule, arrived at independently and correctly.
    "tests/baseline_guard.py": {"path.startswith(immutable_prefix)"},
    # Document-root checks. The roots end in "› ", which is itself a break, so
    # these cannot straddle; they answer "is this label in the right document".
    "tests/test_confirmatory_set.py": {
        'str(case["expected_section_prefix"]).startswith(root)',
        'str(case["also_contains"]).startswith(root)',
    },
    # Category checks — "is this heading an article at all", not "is this the
    # article named X". Over-matching is the intent: `article` should select
    # Article 1 and Article 10 alike.
    "tests/test_ingest_real_corpus.py": {
        's.heading_path[-1].lower().startswith("article")',
        's.heading_path[1].lower().startswith("annex")',
    },
    # The literal ends at a separator, so it cannot straddle a component.
    "tests/test_retrieval_search.py": {
        'rows[0].section_path.startswith("Synthetic Regulation › ")'
    },
    # The test OF the rule, which necessarily compares the rule against the raw
    # operation it narrows.
    "tests/test_section_match.py": {"path.startswith(prefix)"},
}


def _python_files() -> list[Path]:
    return sorted(
        path
        for directory in SOURCE_DIRS
        for path in (REPO_ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _prefix_comparisons() -> dict[str, set[str]]:
    """Every `.startswith(...)` whose subject looks like a path, by file."""
    found: dict[str, set[str]] = {}
    for path in _python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == RULE_MODULE:
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "startswith":
                continue
            segment = ast.get_source_segment(source, node) or ""
            subject = ast.get_source_segment(source, func.value) or ""
            arg = ast.get_source_segment(source, node.args[0]) if node.args else ""
            looks_pathish = any(
                token in subject for token in ("section_path", "heading_path", "path")
            ) or (arg or "").strip() in {"prefix", "root", "immutable_prefix"}
            if looks_pathish:
                found.setdefault(rel, set()).add(" ".join(segment.split()))
    return found


def test_section_paths_are_compared_only_through_the_shared_rule() -> None:
    """The sixth site fails here rather than in a future sweep.

    **Scope, stated so the guarantee is not read wider than it is:** this catches
    a `startswith` whose subject mentions a path or whose argument is named
    `prefix`/`root`. That is the shape all five sites had. It does not catch
    every conceivable prefix comparison — a `SQL LIKE`, a slice, or a regex would
    pass — so it is a tripwire on the known shape, not a proof of absence.
    """
    unexpected = {
        rel: sorted(calls - ALLOWED_PREFIX_COMPARISONS.get(rel, set()))
        for rel, calls in _prefix_comparisons().items()
        if calls - ALLOWED_PREFIX_COMPARISONS.get(rel, set())
    }
    assert not unexpected, (
        "path-like prefix comparison outside scripts/section_match.py:\n"
        + json.dumps(unexpected, indent=2)
        + "\n\nUse matches_section(), or add it to ALLOWED_PREFIX_COMPARISONS with a reason."
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """An allowlist that outlives its entries stops being read.

    Without this, a deleted call leaves a permitted pattern behind, and the next
    reviewer sees a longer list of exceptions than the code actually needs —
    which is how an allowlist becomes a rubber stamp.
    """
    live = _prefix_comparisons()
    for rel, allowed in ALLOWED_PREFIX_COMPARISONS.items():
        stale = allowed - live.get(rel, set())
        assert not stale, f"{rel} no longer contains {sorted(stale)}"


def test_the_gold_label_preflight_runs_before_any_embedding_is_billed() -> None:
    """Order matters and is asserted on the source, because it cannot be
    asserted on behaviour without a corpus and an API key.

    A label that names no section, or names one mid-word, is refused while
    refusing is still free. Two such labels have appeared in ninety questions,
    so this path is exercised at a rate of roughly one per block.
    """
    source = (REPO_ROOT / "scripts" / "interim_r.py").read_text(encoding="utf-8")
    measure = source.index("async def measure(")
    preflight = source.index("await unresolvable_prefixes(", measure)
    embedder = source.index("OpenAIEmbeddingClient()", measure)
    assert preflight < embedder, (
        "the gold-label pre-flight must run before the embedder is constructed; "
        "otherwise a bad label is caught after the block has been paid for"
    )


def test_every_interim_artifact_records_an_exact_query_plan() -> None:
    """A recall figure measured under an approximate index is not the same number.

    `recall@8` is the pre-registered primary metric. Under an approximate scan
    some fraction of every miss is an index artifact rather than a retrieval
    failure, and nothing downstream can separate the two — so the plan is
    recorded per run and asserted here. If a future run flips to the HNSW index,
    this fails loudly instead of the numbers quietly changing meaning.
    """
    artifacts = sorted((REPO_ROOT / "evals").glob("interim-block-*.json"))
    if not artifacts:
        pytest.skip("no interim artifacts yet")
    for path in artifacts:
        payload = json.loads(path.read_text(encoding="utf-8"))
        plan = payload.get("query_plan")
        assert plan is not None, f"{path.name} records no query plan"
        assert plan["exact"], f"{path.name} ran under a non-exact plan: {plan['nodes']}"
        assert not plan["hnsw_index_used"], f"{path.name} used the HNSW index: {plan['nodes']}"
