"""POST /query — retrieve, answer, log (SPEC-006 Interface).

Every verdict returns 200 (KD-1): status describes the transport, `verdict`
describes the outcome. Nothing here maps a refusal to an error.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from rag_qa.api.auth import Scope, require
from rag_qa.api.budget import Reservation
from rag_qa.api.context import current_request_id, record_outcome
from rag_qa.api.deps import AppState
from rag_qa.api.errors import (
    ApiError,
    Misconfigured,
    Overloaded,
    UpstreamError,
    ValidationFailed,
    translate,
)
from rag_qa.api.schemas import ErrorResponse, QueryRequest, QueryResponse
from rag_qa.api.sse import SSE_HEADERS, data_frame, error_frame, event_payload, with_heartbeats
from rag_qa.generation.types import AnswerComplete, AnswerEvent
from rag_qa.retrieval.types import RetrievedChunk

logger = logging.getLogger(__name__)

router = APIRouter()

# Generation outlives the client connection on purpose: the tokens were spent
# whether or not anyone was listening, so a disconnect must not lose the
# query_log row. Strong references keep the tasks from being garbage-collected.
_background: set[asyncio.Task[None]] = set()

_SSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        "Server-sent events. Unnamed `data:` frames carrying JSON with a "
                        "`type` discriminator: verdict (first), text, citation, complete "
                        "(last), or error. `: keepalive` comment frames while idle."
                    ),
                }
            }
        }
    },
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.rag
    if state.retriever is None or state.generator is None:
        raise Misconfigured("retrieval or generation is not configured on this app")
    return state


async def _retrieve(state: AppState, payload: QueryRequest) -> list[RetrievedChunk]:
    """Bounded by the semaphore, which guards the pool and nothing else.

    Held only across retrieval: SPEC-004's two connections are released before
    the provider call, and generation holds none — so the multi-second part of a
    request costs no pool at all (KD-10).
    """
    assert state.retriever is not None
    try:
        await asyncio.wait_for(
            state.query_semaphore.acquire(),
            timeout=state.settings.query_acquire_timeout_seconds,
        )
    except TimeoutError as exc:
        raise Overloaded(
            "too many concurrent retrievals for the connection pool; retry shortly",
            retry_after=1,
        ) from exc
    try:
        filters = payload.filters.to_filters() if payload.filters else None
        return await state.retriever.retrieve(payload.question, payload.k, filters)
    except Exception as exc:
        translated = translate(exc)
        if translated is not None:
            raise translated from exc
        raise
    finally:
        state.query_semaphore.release()


@router.post(
    "/query",
    response_model=QueryResponse,
    responses=_SSE_RESPONSES,
    summary="Ask a question against the ingested corpus",
    dependencies=[Depends(require(Scope.READ))],
)
async def query(request: Request, payload: QueryRequest) -> Any:
    state = _state(request)
    assert state.generator is not None

    if not payload.question.strip():
        raise ValidationFailed("question must not be blank")
    # Before the provider call: a breaker that trips after paying for the answer
    # is not a breaker (KD-16). This is the cheap shed — is the money gone? —
    # and it runs before retrieval so an exhausted budget costs an embedding
    # call and two connections less than it otherwise would.
    await state.budget.check()

    started = time.perf_counter()
    chunks = await _retrieve(state, payload)

    # The second half of the ceiling, and it has to be here rather than beside
    # the check above: the amount to claim is an upper bound on *this* prompt,
    # and the prompt does not exist until retrieval has run (KD-16 amendment 5).
    # The window it covers — reserve here, settle when the provider returns — is
    # also the whole of the exposure, since retrieval is milliseconds and
    # generation is seconds.
    reservation = await _reserve(state, payload, chunks)

    if not payload.stream:
        try:
            try:
                answer = await state.generator.answer(payload.question, chunks)
            except ApiError:
                raise
            except Exception as exc:
                translated = translate(exc)
                if translated is not None:
                    raise translated from exc
                # The provider is the upstream boundary; a transport failure
                # there is 502, and only the exception *type* crosses back to
                # the caller.
                raise UpstreamError(f"provider call failed ({type(exc).__name__})") from exc
            reservation.settle(answer.cost_usd)
            state.metrics.observe_answer(
                str(answer.verdict), answer.prompt_tokens, answer.completion_tokens, answer.cost_usd
            )
            state.metrics.observe_query_latency(time.perf_counter() - started)
            record_outcome(verdict=str(answer.verdict))
            return QueryResponse.build(answer, chunks, current_request_id())
        finally:
            # Unconditional, and a no-op after `settle`. The paths that matter
            # are the ones not written above: a translated ApiError, an
            # UpstreamError, and cancellation when the caller disconnects mid
            # generation — each of which leaves the claim held forever without
            # this line, and none of which a success-path release would cover.
            reservation.release()

    # The pump owns the reservation from the moment it exists, because
    # generation deliberately outlives the client connection: a body iterator
    # that is never driven would strand the claim, and the task always runs.
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    try:
        events = state.generator.stream_answer(payload.question, chunks)
        task = asyncio.create_task(_pump(state, events, queue, started, reservation))
    except BaseException:
        reservation.release()
        raise
    _background.add(task)
    task.add_done_callback(_background.discard)

    return StreamingResponse(
        _sse_body(state, queue),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


async def _reserve(
    state: AppState, payload: QueryRequest, chunks: Sequence[RetrievedChunk]
) -> Reservation:
    """Claim the worst case this answer could cost, before it is spent.

    The bound is only computed when a ceiling is configured — with no budget
    there is nothing to reserve against, and rendering the prompt a second time
    to measure it would be work done for no one.
    """
    assert state.generator is not None
    amount = (
        state.generator.max_cost(payload.question, chunks) if state.budget.enabled else Decimal("0")
    )
    return await state.budget.reserve(amount)


async def _pump(
    state: AppState,
    events: AsyncIterator[AnswerEvent],
    queue: "asyncio.Queue[tuple[str, Any]]",
    started: float,
    reservation: Reservation,
) -> None:
    """Drive generation independently of the client connection.

    Owns the reservation for the whole of the provider call, and releases it in
    the `finally` that already existed for the queue sentinel — the one place
    reached by a clean finish, a stream that dies mid-answer, and cancellation
    when this task is torn down.
    """
    try:
        async for event in events:
            queue.put_nowait(("event", event))
            if isinstance(event, AnswerComplete):
                answer = event.answer
                reservation.settle(answer.cost_usd)
                state.metrics.observe_answer(
                    str(answer.verdict),
                    answer.prompt_tokens,
                    answer.completion_tokens,
                    answer.cost_usd,
                )
                state.metrics.observe_query_latency(time.perf_counter() - started)
                record_outcome(verdict=str(answer.verdict))
    except Exception as exc:
        # The client gets a terminal frame; without this the server kept nothing
        # at all about the one failure a user experiences as a broken answer.
        # Logged where it is caught, before the frame is queued, because the
        # frame's delivery depends on a client that may already be gone.
        translated = translate(exc)
        code = translated.code if translated is not None else "upstream_error"
        record_outcome(error_code=code)
        state.metrics.observe_error(code)
        logger.error(
            "stream failed after the response began",
            extra={"error_code": code, "exception_type": type(exc).__name__},
        )
        queue.put_nowait(("error", exc))
    finally:
        # Before the sentinel, so the claim is already gone by the time anything
        # downstream can observe the stream as finished. Reached by a clean
        # finish, by a stream that dies after the first frame, and by
        # cancellation when the task is torn down — which is the disconnect
        # shape, and the one no success-path release would have covered.
        reservation.release()
        queue.put_nowait(("done", None))


async def _sse_body(state: AppState, queue: "asyncio.Queue[tuple[str, Any]]") -> AsyncIterator[str]:
    """Render frames from whatever the pump has produced.

    Creating the pump task is the caller's job, not this generator's: a
    `StreamingResponse` body that is never driven would never run this function
    at all, and the task holds the reservation.
    """

    async def frames() -> AsyncIterator[str]:
        while True:
            kind, item = await queue.get()
            if kind == "done":
                return
            if kind == "error":
                translated = translate(item) or UpstreamError(
                    f"provider call failed ({type(item).__name__})"
                )
                # Headers already went out with a 200, so the failure has to be
                # in-band; a status code is no longer available (KD-3).
                yield error_frame(translated.code, translated.message)
                return
            yield data_frame(event_payload(item))

    async for frame in with_heartbeats(frames(), state.settings.sse_heartbeat_seconds):
        yield frame
