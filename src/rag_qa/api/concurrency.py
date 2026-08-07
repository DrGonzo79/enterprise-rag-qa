"""Query concurrency bound, derived from the pool (SPEC-006 KD-10).

**The deadlock this was built for no longer exists** (2026-08-05, SPEC-004
KD-17). SPEC-004 opened *two* sessions per `retrieve()` and gathered them: with
ten connections per replica, ten simultaneous requests could each take their
first connection, exhaust the pool, and all block awaiting a second that nobody
could release. **A request that holds exactly one connection cannot deadlock** —
it always progresses to release.

The bound is kept anyway, and the reason changed rather than disappeared: it is
now a **queueing** bound, not a deadlock guard. Without it, requests beyond the
pool's capacity wait at `pool_timeout` and fail with a 500 instead of being shed
with a 503 at the door, and the reserve for single-checkout consumers stops
meaning anything. Whether the *number* is still right is SPEC-006 KD-10's
question, not this module's — see the amendment there.
"""

from rag_qa.db.engine import POOL_MAX_OVERFLOW, POOL_SIZE

# The divisor is enumerated for the same reason RESERVED is, and for a sharper
# one: RESERVED guards the numerator, but KD-10's deferred risk lives *here* —
# "if anything ever adds a second concurrent connection consumer to a request,
# the arithmetic silently changes and the deadlock returns." A hardcoded number
# is exactly that silence. Naming each concurrent checkout makes adding one a
# visible edit, and AC-8 *measures* the real peak against a live pool, so the
# constant cannot drift from the code it claims to describe.
#
# It went 2 -> 1 on 2026-08-05 by DELETING A LINE, which is the point of the
# enumeration: nobody edited an arithmetic constant, and AC-8 re-measured the
# peak rather than being told it.
QUERY_CONNECTIONS: tuple[str, ...] = (
    "dense search — the embedder identity check rides the same session",
)
CONNECTIONS_PER_QUERY = len(QUERY_CONNECTIONS)

# What the margin reserves, enumerated so it is auditable rather than magic
# (KD-10, review amendment 3). The reserve exists so single-checkout consumers
# are never starved by a saturated query load; they cannot deadlock, because a
# caller that takes exactly one connection and releases it always progresses.
#
# Deliberately NOT reserved, each with its consequence recorded in KD-10:
#   - /metrics       0 connections by KD-9. If that ever changes, add it here.
#   - budget refresh single-checkout, once per TTL per replica. The daily and
#                    monthly windows are one statement deliberately, so adding
#                    the monthly ceiling did not add a checkout.
#   - /ingest        admin-triggered, single-flight, rare. If it becomes routine,
#                    add it here and the bound drops by one.
#   - migrations     run out-of-band via the CLI, never in the serving process.
RESERVED_CONNECTIONS: tuple[str, ...] = (
    "query_log write",
    "/health readiness probe",
)
RESERVED = len(RESERVED_CONNECTIONS)


def max_concurrent_queries(
    pool_size: int = POOL_SIZE, max_overflow: int = POOL_MAX_OVERFLOW
) -> int:
    """Derived, never typed in — raising the pool without revisiting this fails
    AC-8 rather than silently reintroducing the deadlock."""
    return max(1, (pool_size + max_overflow - RESERVED) // CONNECTIONS_PER_QUERY)


MAX_CONCURRENT_QUERIES = max_concurrent_queries()
