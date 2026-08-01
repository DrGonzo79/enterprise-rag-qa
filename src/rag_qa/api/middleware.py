"""Request-id middleware (SPEC-006 KD-5, KD-6).

Pure ASGI rather than Starlette's BaseHTTPMiddleware, for two reasons that both
matter here: BaseHTTPMiddleware runs the downstream app in a separate task, which
breaks ContextVar propagation back to this scope, and it wraps streaming
responses in a way that interferes with SSE.
"""

import logging
import time
from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import suppress
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from rag_qa.api.context import (
    REQUEST_ID_HEADER,
    new_outcome,
    record_outcome,
    request_id_var,
    sanitize_request_id,
)
from rag_qa.api.errors import ApiError, envelope, translate
from rag_qa.api.metrics import Metrics

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
Outcome = MutableMapping[str, Any]


UNMATCHED_ROUTE = "__unmatched__"


def route_label(scope: Scope) -> str:
    """The matched route template, or one constant for everything else.

    **The original implementation recorded `scope["path"]` verbatim**, justified
    on the grounds that this API has a fixed, small set of paths and none carry
    parameters. That premise holds for *matched* routes and fails completely for
    the 404 space: `GET /a1`, `/a2`, … each created a new key in a
    process-lifetime `Counter` that is never evicted, required no authentication
    (routing precedes auth), and was reachable by anyone who could open a socket.
    Unbounded dictionary growth in the serving process first, a scrape response
    that grows with it second, a Prometheus cardinality explosion third — in the
    same process that enforces the spend ceiling, whose correctness depends on it
    staying up.

    **The rule the original reasoning was missing:** a label is safe when its
    value space is enumerable from the code, and a path is only enumerable
    *after* it matches something. Both branches below are enumerable — FastAPI's
    `APIRoute` publishes its template in the scope, and a route that matched
    without publishing one (`/docs`, `/openapi.json`) is still one of a fixed set
    the app registered. Anything else is a single constant.
    """
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str):
        return template
    # Matched a framework route that publishes no template. `endpoint` is set
    # only on a match, so this stays bounded by the registered route table —
    # except for a parameterized one, which would leak a concrete path, so it is
    # excluded explicitly rather than by assuming none is ever added.
    if scope.get("endpoint") is not None and not scope.get("path_params"):
        return str(scope.get("path", UNMATCHED_ROUTE))
    return UNMATCHED_ROUTE


PROBE_ROUTES = frozenset({"/health", "/healthz"})

request_logger = logging.getLogger("rag_qa.api.request")


class RequestContextMiddleware:
    """Request id, error envelope, and the completion record (SPEC-008).

    **The completion record is emitted here rather than by its own middleware,
    and the reason is ordering rather than convenience.** It has to run *inside*
    the request-id context — otherwise the one line naming the outcome is the
    only line in the trace that cannot be joined to the rest — and *after* the
    error envelope has been chosen, or an unhandled exception would be recorded
    before anyone decided it was a 500 with a code. A separate middleware lands
    on one side of that or the other; there is no position that is both.
    """

    def __init__(
        self, app: Callable[[Scope, Receive, Send], Awaitable[None]], metrics: Metrics
    ) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = sanitize_request_id(inbound)
        token = request_id_var.set(request_id)
        outcome = new_outcome()
        began = time.perf_counter()

        started = False
        status = 500

        async def send_with_header(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        except Exception as exc:
            # Starlette's ServerErrorMiddleware sits *outside* this one, so a
            # response it rendered would carry neither the request-id header nor
            # the metrics counter. Unhandled errors are finished here instead,
            # where the context is still set and the send wrapper is in place.
            if started:
                raise  # headers already went out; nothing can be retracted
            error = translate(exc) or ApiError("internal error")
            if error.code == "internal_error":
                # Never leak a traceback, SQL, or a file path to a caller.
                logger.exception("unhandled error serving %s", scope.get("path"))
            record_outcome(error_code=error.code)
            body_status, body, headers = envelope(error, request_id)
            status = body_status
            await JSONResponse(body, status_code=body_status, headers=headers)(
                scope, receive, send_with_header
            )
        finally:
            # The stream has finished by now, not merely its headers — which is
            # what lets an SSE request carry the verdict its background pump
            # resolved seconds after the response started.
            self._finish(scope, status, (time.perf_counter() - began) * 1000, outcome)
            request_id_var.reset(token)

    def _finish(self, scope: Scope, status: int, duration_ms: float, outcome: Outcome) -> None:
        """Telemetry must never be able to break a response.

        This runs in a `finally`, **after** `http.response.start` has gone out —
        so an exception raised here cannot be turned into a response by anything
        upstream; it escapes as a protocol error on a request the caller has
        already been answered. `observe_error` deliberately raises on a code with
        no registry entry (SPEC-008 KD-9), which is the right behaviour at the
        unit level and the wrong thing to let loose here. No path reaches it with
        an unregistered code today — the class check makes subclasses impossible
        and the middleware overwrites a rogue dynamic code with `internal_error`
        before this runs — but "unreachable through three coincidences" is a
        weaker guarantee than "cannot break the response", and the second is one
        `try` away.
        """
        try:
            self._record(scope, status, duration_ms, outcome)
        except Exception:
            # Reported on this module's own logger, never through
            # `request_logger` or a `Metrics` call that may be the thing failing:
            # a telemetry outage whose only symptom is a missing telemetry record
            # is invisible exactly when it matters. The counter is a second
            # channel for the same reason, guarded so a broken `Metrics` cannot
            # turn the report into a second exception.
            logger.exception("failed to record the completion of %s", scope.get("path"))
            with suppress(Exception):
                self.metrics.telemetry_failures += 1

    def _record(self, scope: Scope, status: int, duration_ms: float, outcome: Outcome) -> None:
        route = route_label(scope)
        code = outcome.get("error_code")
        if isinstance(code, str):
            self.metrics.observe_error(code)
            if code == "overloaded":
                self.metrics.observe_shed()
            ceiling = outcome.get("ceiling")
            if code == "budget_exhausted" and isinstance(ceiling, str):
                self.metrics.observe_budget_trip(ceiling)
            # Counted apart from a trip on purpose. Both refuse a visitor, and
            # that is where the resemblance ends: a trip lasts until a UTC
            # boundary, pressure lasts one generation. Summing them would cost
            # the trip counter the meaning an operator pages on, and leaving
            # pressure to the headroom gauges would make it unobservable — a
            # 15s scrape cannot see a 3s spike.
            if code == "budget_pressure":
                self.metrics.observe_budget_pressure()

        # A readiness probe every 10s across three replicas is ~26,000 records a
        # day saying "still ok": volume that costs money to ship, buries what
        # matters, and teaches an operator to filter out the logger that would
        # have told them the corpus went empty. The request is still *counted*,
        # which is the right representation for a frequent uninteresting event.
        if route in PROBE_ROUTES:
            level = logging.DEBUG
        elif status >= 500:
            level = logging.WARNING
        else:
            level = logging.INFO

        fields: dict[str, Any] = {
            "method": str(scope.get("method", "")),
            "route": route,
            "status": status,
            "duration_ms": round(duration_ms, 2),
        }
        for key in ("verdict", "error_code"):
            value = outcome.get(key)
            if value is not None:
                fields[key] = value
        request_logger.log(level, "http.request", extra=fields)


class MetricsMiddleware:
    """Request counts by route template and status, in process (KD-9).

    Labels come from the matched route, never the raw path — see `route_label`.
    """

    def __init__(
        self, app: Callable[[Scope, Receive, Send], Awaitable[None]], metrics: Metrics
    ) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_metrics(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Read at response time: the router has updated the scope by now,
                # so the template is available where the raw path used to be.
                self.metrics.observe_request(route_label(scope), int(message["status"]))
            await send(message)

        await self.app(scope, receive, send_with_metrics)
