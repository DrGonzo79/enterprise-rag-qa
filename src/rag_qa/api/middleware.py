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


class MetricsMiddleware:
    """Request counts by path and status, in process (KD-9).

    Paths are recorded verbatim because this API has a fixed, small set of them
    and none carry parameters — so there is no cardinality explosion to guard
    against, and no route-template lookup to get wrong.
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

        path = str(scope.get("path", ""))

        async def send_with_metrics(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.metrics.observe_request(path, int(message["status"]))
            await send(message)

        await self.app(scope, receive, send_with_metrics)
