"""One registry for every way this service declines to answer.

**Why a registry rather than two lists.** SPEC-008 needs a server-side taxonomy
so an operator can tell a budget trip from a shed from an embedder mismatch;
SPEC-006 KD-16 needs a client-facing taxonomy so a visitor sees an explanatory
state rather than a bare 503. Those are the same set viewed from two sides, and
maintained separately they drift in the worst possible way: a new failure mode
gets added to the half its author was looking at, and the other half silently
renders it as a generic error — or counts it as one. The registry is the single
list; `ApiError` subclasses validate against it at class-creation time, so adding
a failure mode to one half only is not something a reviewer has to catch.

**What is client-facing and what is not.** Every entry carries a `presentation`
and a `reset` kind, which is what a frontend needs to render a condition without
keeping its own copy of the list. Neither carries figures: `public_message` names
*what* happened and never *how much*. SPEC-006 KD-8 refuses to publish the cost
meter because an unauthenticated spend number is a progress bar for anyone trying
to drain the budget — and an error body naming the configured ceiling, the
override, and the derived value is that same meter, reachable by any caller who
can trigger the error. The figures belong in the log record and in the
admin-scoped `/metrics`, which is where they now live.
"""

from dataclasses import dataclass
from enum import StrEnum


class Presentation(StrEnum):
    """How a client should render the condition — the seam SPEC-009 consumes."""

    EXPLANATORY = "explanatory"
    """The service is deliberately not answering. SPEC-006 KD-16 binds SPEC-009
    to render the full panel here: labeled pre-recorded Q&A, the eval report, the
    architecture, and the reset. This is the state the rejected canned answer was
    trying to serve, solved where it does not corrupt `query_log`."""

    TRANSIENT = "transient"
    """Nothing is wrong with the request or the deployment; try again shortly."""

    DEGRADED = "degraded"
    """The deployment is broken or misconfigured. Not the caller's doing, and not
    something waiting will fix."""

    REQUEST = "request"
    """The caller's request was the problem. Retrying it unchanged will not
    help."""


class Reset(StrEnum):
    """When the condition clears — and whether a countdown can be shown at all.

    **This is the field that cannot always be filled truthfully, and the reason
    it is a kind rather than a timestamp.** A window condition has an honest
    clock: `Retry-After` counts to a boundary that arrives whether or not anyone
    acts. An operator condition has none — an empty corpus, an embedder
    mismatch, a missing pricing row: none of these clear at midnight, and a
    countdown rendered against them is a lie that expires into the same error.
    Making `reset` an enum rather than a nullable time means the "no clock" case
    has a rendering instead of a blank.
    """

    WINDOW = "window"
    """Clears on its own at a known instant; `Retry-After` is accurate."""

    SHORTLY = "shortly"
    """Retryable soon, with no precise time worth quoting."""

    OPERATOR = "operator"
    """Clears only when an operator changes something. No countdown exists."""

    NONE = "none"
    """Nothing to wait for."""


@dataclass(frozen=True)
class ConditionSpec:
    code: str
    status: int
    presentation: Presentation
    reset: Reset
    public_message: str
    """Default caller-facing text. Names what happened, never how much."""

    refusal: bool = False
    """Counted in SPEC-008's failure signal — the ways the service declines to do
    work it would otherwise do. Distinct from a bad request, which was never
    work the service agreed to."""


def _spec(
    code: str,
    status: int,
    presentation: Presentation,
    reset: Reset,
    public_message: str,
    *,
    refusal: bool = False,
) -> tuple[str, ConditionSpec]:
    return code, ConditionSpec(code, status, presentation, reset, public_message, refusal)


CONDITIONS: dict[str, ConditionSpec] = dict(
    (
        # --- refusals: work the service would do, and is declining to ---------
        _spec(
            "budget_exhausted",
            503,
            Presentation.EXPLANATORY,
            Reset.WINDOW,
            "the demo's spending limit for this window has been reached",
            refusal=True,
        ),
        _spec(
            "overloaded",
            503,
            Presentation.TRANSIENT,
            Reset.SHORTLY,
            "too many concurrent requests; retry shortly",
            refusal=True,
        ),
        _spec(
            "embedder_mismatch",
            503,
            Presentation.DEGRADED,
            Reset.OPERATOR,
            "the stored corpus and the query embedder disagree; the corpus needs re-ingesting",
            refusal=True,
        ),
        _spec(
            "empty_corpus",
            503,
            Presentation.DEGRADED,
            Reset.OPERATOR,
            "no corpus has been ingested",
            refusal=True,
        ),
        _spec(
            "upstream_error",
            502,
            Presentation.TRANSIENT,
            Reset.SHORTLY,
            "the model provider could not be reached",
            refusal=True,
        ),
        # --- the deployment is wrong ------------------------------------------
        _spec(
            "misconfigured",
            500,
            Presentation.DEGRADED,
            Reset.OPERATOR,
            "the service is misconfigured",
        ),
        _spec(
            "internal_error",
            500,
            Presentation.DEGRADED,
            Reset.SHORTLY,
            "internal error",
        ),
        # --- the request is wrong ---------------------------------------------
        _spec(
            "unauthenticated",
            401,
            Presentation.REQUEST,
            Reset.NONE,
            "a valid API key is required",
        ),
        _spec(
            "forbidden",
            403,
            Presentation.REQUEST,
            Reset.NONE,
            "the presented key lacks the required scope",
        ),
        _spec(
            "not_found",
            404,
            Presentation.REQUEST,
            Reset.NONE,
            "no such endpoint",
        ),
        _spec(
            "http_error",
            400,
            Presentation.REQUEST,
            Reset.NONE,
            "the request could not be served",
        ),
        _spec(
            "validation_error",
            422,
            Presentation.REQUEST,
            Reset.NONE,
            "the request body is invalid",
        ),
        _spec(
            "ingest_in_progress",
            409,
            Presentation.TRANSIENT,
            Reset.SHORTLY,
            "an ingest is already running",
        ),
        _spec(
            "ingest_too_large",
            413,
            Presentation.REQUEST,
            Reset.NONE,
            "the requested ingest exceeds the synchronous bound; use the CLI",
        ),
    )
)

REFUSALS: frozenset[str] = frozenset(code for code, spec in CONDITIONS.items() if spec.refusal)


def spec_for(code: str) -> ConditionSpec:
    """The registry entry, or a loud failure. A code with no entry has no client
    rendering, which is the drift this module exists to make impossible."""
    try:
        return CONDITIONS[code]
    except KeyError as exc:  # pragma: no cover - the tests assert this cannot happen
        raise KeyError(
            f"error code {code!r} has no entry in CONDITIONS, so it has no client-facing "
            "rendering and no place in the failure signal; add one"
        ) from exc
