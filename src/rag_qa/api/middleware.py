"""Request-id middleware (SPEC-006 KD-5, KD-6).

Pure ASGI rather than Starlette's BaseHTTPMiddleware, for two reasons that both
matter here: BaseHTTPMiddleware runs the downstream app in a separate task, which
breaks ContextVar propagation back to this scope, and it wraps streaming
responses in a way that interferes with SSE.
"""

import logging
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from rag_qa.api.context import REQUEST_ID_HEADER, request_id_var, sanitize_request_id
from rag_qa.api.errors import ApiError, envelope, translate
from rag_qa.api.metrics import Metrics

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class RequestContextMiddleware:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = sanitize_request_id(inbound)
        token = request_id_var.set(request_id)

        started = False

        async def send_with_header(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
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
            status, body, headers = envelope(error, request_id)
            await JSONResponse(body, status_code=status, headers=headers)(
                scope, receive, send_with_header
            )
        finally:
            request_id_var.reset(token)


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
