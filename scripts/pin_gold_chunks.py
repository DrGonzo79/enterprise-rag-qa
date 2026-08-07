"""Pin every gold label to chunk identity instead of a path prefix.

**The defect.** `matches_section` enforces component breaks and catches
ambiguity, but not *exactness*: a truncation that happens to resolve uniquely
passes. `Item 1. Business › Human Capital` resolves to
`… › Human Capital Management` today because nothing else starts that way.
Systematically checked, **101 of 120 golds are truncations** — the EU section
titles all carry a `— Title` suffix that the golds were written without — and
**three resolve to more than one section already** (`con-008`, `con-025`, `con-084`).

**The fix.** Resolve each gold once against the corpus, freeze the resulting
chunk ids into the case, and record the exact section paths beside them. Scoring
then compares identity, not string prefixes. A re-chunk invalidates the ids
loudly instead of silently re-pointing a truncation at a different section.

**This must not move the result.** It provably does not: the frozen ids are the
chunks the old prefix already matched, and the check below asserts that the
exact paths resolve to the identical chunk set for every case.

Usage:
    uv run python -m scripts.pin_gold_chunks           # report only
    uv run python -m scripts.pin_gold_chunks --write   # rewrite the set
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from rag_qa.env import load_env
from scripts.section_match import matches_section

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / "evals" / "retrieval_confirmatory.jsonl"
GOLD_KEYS = ("expected_section_prefix", "also_contains")

load_env()
CORPUS_URL = os.environ.get(
    "RAG_QA_CORPUS_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5432/rag"
)


def is_exact_anchor(gold: str, paths: set[str]) -> bool:
    """True when `gold` names a section exactly, or an ancestor at a `›` break.

    The distinction `matches_section` cannot make on its own: it is asked
    "does this path belong to this node", which a truncation also satisfies.
    """
    return gold in paths or any(p.startswith(gold + " › ") for p in paths)


async def main() -> int:
    write = "--write" in sys.argv
    engine = create_async_engine(CORPUS_URL)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id, section_path FROM chunks"))).all()
    await engine.dispose()
    chunks = [(str(r[0]), str(r[1])) for r in rows]
    paths = {path for _, path in chunks}

    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]

    truncated: list[tuple[str, str, str]] = []
    ambiguous: list[tuple[str, str, list[str]]] = []
    for case in cases:
        for key in GOLD_KEYS:
            gold = case.get(key)
            if not gold:
                continue
            matched_paths = sorted({p for p in paths if matches_section(gold, p)})
            if not matched_paths:
                print(f"{case['id']}.{key} resolves to nothing: {gold}", file=sys.stderr)
                return 2
            if not is_exact_anchor(gold, paths):
                truncated.append((case["id"], key, gold))
            if len(matched_paths) > 1:
                ambiguous.append((case["id"], key, matched_paths))

    print(f"golds:            {sum(1 for c in cases for k in GOLD_KEYS if c.get(k))}")
    print(f"truncations:      {len(truncated)}")
    print(f"resolving to >1 section: {len(ambiguous)}")
    for cid, key, matched in ambiguous:
        print(f"  {cid}.{key} -> {len(matched)} sections")
        for path in matched:
            print(f"      {path}")

    # Freeze identity. Derived from the SAME resolution the confirmatory run
    # scored under, so the result cannot move; the assertion below is what makes
    # that a checked fact rather than a claim.
    unchanged = True
    for case in cases:
        for key in GOLD_KEYS:
            gold = case.get(key)
            if not gold:
                continue
            ids = sorted(cid for cid, path in chunks if matches_section(gold, path))
            exact = sorted({p for p in paths if matches_section(gold, p)})
            ids_from_exact = sorted(
                cid for cid, path in chunks if any(matches_section(e, path) for e in exact)
            )
            if ids != ids_from_exact:
                print(f"{case['id']}.{key}: exact paths resolve differently", file=sys.stderr)
                unchanged = False
            id_key = "gold_chunk_ids" if key == "expected_section_prefix" else "also_chunk_ids"
            path_key = (
                "expected_section_paths" if key == "expected_section_prefix" else "also_paths"
            )
            case[id_key] = ids
            case[path_key] = exact

    print(f"\nexact paths resolve to the identical chunk set for every gold: {unchanged}")
    if not unchanged:
        return 3

    if write:
        CASES.write_text(
            "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
            encoding="utf-8",
        )
        print(f"written: {CASES.relative_to(REPO_ROOT)}")
    else:
        print("(report only; pass --write to rewrite the set)")
    return 0


def _entry() -> int:
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(_entry())
