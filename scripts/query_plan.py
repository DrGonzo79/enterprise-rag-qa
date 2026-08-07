"""Which plan the dense search actually ran under, recorded not assumed.

**Why this exists.** A 0.0011 discrepancy in one block's `MRR@8` was attributed
to HNSW being an approximate index and the planner sometimes choosing it. That
hypothesis was checked and is **false**: the HNSW index is not in the plan and
never has been, because `vector_stmt` orders by `(distance, id)` and an HNSW
index can only order by the distance operator alone. Retrieval in this
repository is an exact sequential scan.

The lesson is not about HNSW. It is that **an eval reporting numbers without
recording the plan that produced them cannot tell an index artifact from a
retrieval failure**, and the difference matters most for `recall@8`, which is
the pre-registered primary metric. So every eval artifact now carries the plan.

`enable_seqscan = off` is applied to eval connections to force the choice
explicitly rather than leave it to the planner's cost model. **Today it changes
nothing** — the tie-break makes the index unusable at any setting — and the
recorded plan says so, which is the point: a pin whose effect is recorded is
diagnosable, and one that is merely assumed is the thing that just cost a day.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_qa.retrieval.search import CANDIDATE_POOL, vector_stmt

# Applied to eval and interim connections. See the module docstring: this
# forces the planner's hand rather than trusting it to be stable across
# statistics changes, corpus growth, or a Postgres upgrade.
EVAL_SERVER_SETTINGS = {"enable_seqscan": "off"}


def _nodes(plan: dict[str, Any]) -> list[str]:
    name = str(plan["Node Type"])
    if plan.get("Index Name"):
        name = f"{name}[{plan['Index Name']}]"
    out = [name]
    for sub in plan.get("Plans", []):
        out.extend(_nodes(sub))
    return out


async def observed_vector_plan(session: AsyncSession, dim: int = 1536) -> dict[str, Any]:
    """EXPLAIN the statement `vector_search` issues, on this connection.

    Uses `vector_stmt` itself rather than a re-typed copy of the SQL, so the
    plan recorded is the plan of the query that runs. A reconstructed query
    would be a second chance to differ, which is the failure this whole module
    is a response to.
    """
    stmt = vector_stmt([0.0] * dim, None, CANDIDATE_POOL)
    compiled = stmt.compile(
        dialect=session.bind.dialect,  # type: ignore[union-attr]
        compile_kwargs={"literal_binds": True},
    )
    explain = text(f"EXPLAIN (FORMAT JSON) {compiled}")

    async def plan_now() -> list[str]:
        return _nodes((await session.execute(explain)).scalar_one()[0]["Plan"])

    pinned = await plan_now()
    # The plan the API would get, recorded beside it so the gap between what the
    # eval measures and what the demo runs is visible rather than assumed away.
    await session.execute(text("SET LOCAL enable_seqscan = on"))
    default = await plan_now()
    await session.execute(text("SET LOCAL enable_seqscan = off"))

    def hnsw(nodes: list[str]) -> bool:
        return any("ix_chunks_embedding_hnsw" in n for n in nodes)

    return {
        "nodes": pinned,
        "nodes_under_planner_defaults": default,
        # **Exact means the ordering came from a Sort over every candidate row,
        # not from an approximate index.** The first definition of this keyed on
        # "Seq Scan" and was wrong under the pin, where the same exact ordering
        # is produced by a btree scan feeding a Sort -- a check that named one
        # implementation of exactness rather than exactness.
        "exact": not hnsw(pinned) and "Sort" in pinned,
        "hnsw_index_used": hnsw(pinned),
        "hnsw_index_used_under_planner_defaults": hnsw(default),
        "plan_differs_from_planner_default": pinned != default,
        "enable_seqscan": (await session.execute(text("SHOW enable_seqscan"))).scalar_one(),
        "ef_search": (await session.execute(text("SHOW hnsw.ef_search"))).scalar_one(),
    }
