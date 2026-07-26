"""Exception -> status mapping and the error envelope (SPEC-006 Interface).

The organizing rule is Key decision 1: **HTTP status describes the transport;
`verdict` describes the outcome.** Nothing in this module maps a model verdict —
a refusal, a provider decline, a truncation — to a non-200 status. Those are
outcomes of a request that worked.
"""

from rag_qa.api.conditions import spec_for
from rag_qa.generation.types import UnknownModelError
from rag_qa.retrieval.types import EmbedderMismatchError, EmptyCorpusError


class ApiError(Exception):
    """An error with a stable machine-readable code and an HTTP status.

    Every subclass is checked against `CONDITIONS` at class-creation time, so a
    failure mode cannot be added to the server side without also acquiring a
    client-facing rendering. That check runs at import, not in a test, because
    the drift it prevents is the kind a reviewer waves through.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        spec = spec_for(cls.code)
        if spec.status != cls.status_code:
            raise TypeError(
                f"{cls.__name__}.status_code={cls.status_code} disagrees with CONDITIONS"
                f"[{cls.code!r}].status={spec.status}"
            )

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class Unauthenticated(ApiError):
    status_code = 401
    code = "unauthenticated"


class Forbidden(ApiError):
    """Authenticated, but the presented key lacks the scope. Distinct from 401
    on purpose: 401 says *authenticate*, 403 says *that will not help* — and
    collapsing them sends a caller into a retry loop that cannot succeed."""

    status_code = 403
    code = "forbidden"


class Overloaded(ApiError):
    status_code = 503
    code = "overloaded"


class BudgetExhausted(ApiError):
    """A spend ceiling was reached (KD-16). A transport-level condition, not a
    verdict: under the ceiling the question was never asked.

    `ceiling` names which window stopped the request — `daily` or `monthly` — so
    SPEC-008's failure signal can label the counter. It is deliberately *not* in
    the message: which ceiling tripped is operator information, and the caller
    only needs to know that the demo is not answering and when it resumes.
    """

    status_code = 503
    code = "budget_exhausted"

    def __init__(
        self, message: str, *, retry_after: int | None = None, ceiling: str = "daily"
    ) -> None:
        super().__init__(message, retry_after=retry_after)
        self.ceiling = ceiling


class IngestInProgress(ApiError):
    status_code = 409
    code = "ingest_in_progress"


class IngestTooLarge(ApiError):
    status_code = 413
    code = "ingest_too_large"


class ValidationFailed(ApiError):
    """A request the schema accepts but the handler rejects — a whitespace-only
    question passes `min_length=1` and is still not a question."""

    status_code = 422
    code = "validation_error"


class UpstreamError(ApiError):
    status_code = 502
    code = "upstream_error"


class Misconfigured(ApiError):
    status_code = 500
    code = "misconfigured"


# Retrieval faults are operational, not bad requests: the corpus and the query
# embedder disagree, or there is no corpus at all (SPEC-004).
_TRANSLATIONS: tuple[tuple[type[Exception], type[ApiError], str], ...] = (
    (EmbedderMismatchError, Overloaded, "embedder_mismatch"),
    (EmptyCorpusError, Overloaded, "empty_corpus"),
    (UnknownModelError, Misconfigured, "misconfigured"),
)


def envelope(
    error: ApiError, request_id: str
) -> tuple[int, dict[str, dict[str, str]], dict[str, str]]:
    """Status, body, headers — pure, so the middleware and the exception
    handlers cannot render the same error two different ways.

    `presentation` and `reset` come from the registry rather than from the
    raiser, which is what stops a client keeping its own copy of the taxonomy:
    SPEC-009 reads how to render a condition off the wire instead of matching on
    `code` against a list that can fall behind this one.
    """
    spec = spec_for(error.code)
    body = {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": request_id,
            "presentation": str(spec.presentation),
            "reset": str(spec.reset),
        }
    }
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else {}
    return error.status_code, body, headers


def translate(exc: Exception) -> ApiError | None:
    """Map a library exception onto an ApiError, or None if it is not ours."""
    for source, target, code in _TRANSLATIONS:
        if isinstance(exc, source):
            error = target(str(exc))
            error.code = code
            return error
    return None
