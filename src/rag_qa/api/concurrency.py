"""Query concurrency bound, derived from the pool (SPEC-006 KD-10).

SPEC-004 opens **two** sessions per `retrieve()` and gathers them. With ten
connections per replica (SPEC-002 KD-8 — a bound set by Azure's max_connections,
not a preference), ten simultaneous requests can each take their *first*
connection, exhaust the pool, and then all block awaiting a second that nobody
can release. Every request then fails at `pool_timeout` under a load the
arithmetic says should be fine.

The pool cannot be enlarged, so concurrency is bounded above it instead.
"""

from rag_qa.db.engine import POOL_MAX_OVERFLOW, POOL_SIZE

# The divisor is enumerated for the same reason RESERVED is, and for a sharper
# one: RESERVED guards the numerator, but KD-10's deferred risk lives *here* —
# "if anything ever adds a second concurrent connection consumer to a request,
# the arithmetic silently changes and the deadlock returns." A hardcoded 2 is
# exactly that silence. Naming each concurrent checkout makes adding one a
# visible edit, and AC-8 *measures* the real peak against a live pool, so the
# constant cannot drift from the code it claims to describe.
QUERY_CONNECTIONS: tuple[str, ...] = (
    "vector branch (SPEC-004 KD-5)",
    "full-text branch — embedder identity check rides the same session",
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
