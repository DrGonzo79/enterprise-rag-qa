"""POST /query — retrieve, answer, log (SPEC-006 Interface).

Every verdict returns 200 (KD-1): status describes the transport, `verdict`
describes the outcome. Nothing here maps a refusal to an error.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from rag_qa.api.auth import Scope, require
from rag_qa.api.context import current_request_id
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
    # is not a breaker (KD-16).
    await state.budget.check()

    started = time.perf_counter()
    chunks = await _retrieve(state, payload)

    if not payload.stream:
        try:
            answer = await state.generator.answer(payload.question, chunks)
        except ApiError:
            raise
        except Exception as exc:
            translated = translate(exc)
            if translated is not None:
                raise translated from exc
            # The provider is the upstream boundary; a transport failure there is
            # 502, and only the exception *type* crosses back to the caller.
            raise UpstreamError(f"provider call failed ({type(exc).__name__})") from exc
        state.budget.record(answer.cost_usd)
        state.metrics.observe_answer(
            str(answer.verdict), answer.prompt_tokens, answer.completion_tokens, answer.cost_usd
        )
        state.metrics.observe_query_latency(time.perf_counter() - started)
        return QueryResponse.build(answer, chunks, current_request_id())

    return StreamingResponse(
        _sse_body(state, payload, chunks, started),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


async def _pump(
    state: AppState,
    events: AsyncIterator[AnswerEvent],
    queue: "asyncio.Queue[tuple[str, Any]]",
    started: float,
) -> None:
    """Drive generation independently of the client connection."""
    try:
        async for event in events:
            queue.put_nowait(("event", event))
            if isinstance(event, AnswerComplete):
                answer = event.answer
                state.budget.record(answer.cost_usd)
                state.metrics.observe_answer(
                    str(answer.verdict),
                    answer.prompt_tokens,
                    answer.completion_tokens,
                    answer.cost_usd,
                )
                state.metrics.observe_query_latency(time.perf_counter() - started)
    except Exception as exc:
        queue.put_nowait(("error", exc))
    finally:
        queue.put_nowait(("done", None))


async def _sse_body(
    state: AppState, payload: QueryRequest, chunks: Sequence[RetrievedChunk], started: float
) -> AsyncIterator[str]:
    assert state.generator is not None
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    events = state.generator.stream_answer(payload.question, chunks)
    task = asyncio.create_task(_pump(state, events, queue, started))
    _background.add(task)
    task.add_done_callback(_background.discard)

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
