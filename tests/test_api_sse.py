"""SSE framing and the buffered verdict token (SPEC-006 AC-5).

The verdict token is buffered server-side of this boundary (SPEC-005 AC-7a), so
it must never appear on the wire in any form — including as a partial, which is
the realistic failure when a provider chunk boundary lands inside `ANSWERED`.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest

from api_harness import StubRetriever, build_app, data_payloads, sse_frames
from rag_qa.generation.clients.base import StopKind, TextChunk, Usage
from test_generation_service import FakeLLMClient

BODY = "Providers must comply [1] with Article 1."
VERDICT_TOKENS = ("ANSWERED", "INSUFFICIENT_EVIDENCE")


def splits(response: str) -> list[list[str]]:
    """Every split point of the response, one two-chunk stream each."""
    return [[response[:i], response[i:]] for i in range(1, len(response))]


async def stream(app, payload=None):  # type: ignore[no-untyped-def]
    return await sse_frames(app, payload or {"question": "What applies?", "stream": True})


# --- framing ------------------------------------------------------------------


async def test_headers_and_frame_shape() -> None:
    app = build_app(client=FakeLLMClient(f"ANSWERED\n{BODY}"))
    response, frames = await stream(app)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    # A buffering proxy silently defeats streaming.
    assert response.headers["x-accel-buffering"] == "no"

    for frame in frames:
        assert frame.startswith("data: ") or frame.startswith(":"), frame
        # SSE terminates a field at \n: a raw newline would split one logical
        # event across two frames. JSON encoding makes that impossible.
        assert "\n" not in frame


async def test_event_order_and_terminal_complete() -> None:
    app = build_app(client=FakeLLMClient(f"ANSWERED\n{BODY}"))
    _, frames = await stream(app)
    events = data_payloads(frames)
    kinds = [event["type"] for event in events]

    assert kinds[0] == "verdict"
    assert kinds[-1] == "complete"
    assert "citation" in kinds
    assert events[0]["verdict"] == "answered"
    assert events[-1]["usage"]["completion_tokens"] == 80
    assert events[-1]["usage"]["generator_identity"] == "anthropic:claude-sonnet-5"

    citation = next(event for event in events if event["type"] == "citation")
    assert citation["section_path"]  # section_path in BOTH modes


async def test_multiline_answer_stays_one_frame_per_event() -> None:
    """The framing hazard JSON encoding exists to prevent."""
    app = build_app(client=FakeLLMClient("ANSWERED\nFirst line [1].\n\nSecond paragraph."))
    _, frames = await stream(app)
    text = "".join(e["text"] for e in data_payloads(frames) if e["type"] == "text")
    assert text == "First line [1].\n\nSecond paragraph."


# --- the verdict token never crosses the boundary (AC-5) ----------------------


@pytest.mark.parametrize("slices", splits(f"ANSWERED\n{BODY}"), ids=lambda s: f"split@{len(s[0])}")
async def test_verdict_never_reaches_the_wire_at_any_split_point(slices: list[str]) -> None:
    app = build_app(client=FakeLLMClient(stream_slices=slices))
    _, frames = await stream(app)
    events = data_payloads(frames)

    text = "".join(event["text"] for event in events if event["type"] == "text")
    # Exact equality catches a leak of ANY length; a "no full token" check does not.
    assert text == BODY
    for frame in frames:
        for token in VERDICT_TOKENS:
            assert token not in frame


async def test_body_starting_with_the_verdicts_own_letter() -> None:
    """Where a prefix-matching implementation false-positives: 'A' is both the
    first letter of ANSWERED and an ordinary first letter of prose."""
    body = "Answering requires Article 6 [1]."
    app = build_app(
        client=FakeLLMClient(stream_slices=["ANSWE", "RED\nAnswering ", "requires Article 6 [1]."])
    )
    _, frames = await stream(app)
    text = "".join(e["text"] for e in data_payloads(frames) if e["type"] == "text")
    assert text == body
    assert "ANSWERED" not in "".join(frames)


async def test_first_chunk_containing_newline_and_body_together() -> None:
    app = build_app(client=FakeLLMClient(stream_slices=[f"ANSWERED\n{BODY}"]))
    _, frames = await stream(app)
    events = data_payloads(frames)
    assert events[0] == {"type": "verdict", "verdict": "answered", "provisional": True}
    assert "".join(e["text"] for e in events if e["type"] == "text") == BODY


async def test_verdict_line_with_trailing_spaces() -> None:
    app = build_app(client=FakeLLMClient(f"ANSWERED   \n{BODY}"))
    _, frames = await stream(app)
    events = data_payloads(frames)
    assert events[0]["verdict"] == "answered"
    assert "".join(e["text"] for e in events if e["type"] == "text") == BODY


async def test_stream_ending_mid_verdict_yields_error_and_zero_text_frames() -> None:
    app = build_app(client=FakeLLMClient(stream_slices=["ANSWE"]))
    _, frames = await stream(app)
    events = data_payloads(frames)
    assert events[0] == {"type": "verdict", "verdict": "error", "provisional": True}
    assert [event for event in events if event["type"] == "text"] == []
    assert events[-1]["type"] == "complete"


# --- heartbeats and in-band failure ------------------------------------------


class SlowStreamClient(FakeLLMClient):
    """Pauses between slices so the heartbeat path is reachable deterministically."""

    def __init__(self, slices: list[str], pause: float) -> None:
        super().__init__(stream_slices=slices)
        self._pause = pause

    @asynccontextmanager
    async def stream(self, system: str, user: str, max_tokens: int):  # type: ignore[no-untyped-def]
        self.calls.append((system, user, max_tokens))
        slices, pause = list(self._stream_slices or []), self._pause

        async def events():  # type: ignore[no-untyped-def]
            for piece in slices:
                await asyncio.sleep(pause)
                yield TextChunk(piece)
            yield Usage(1200, 80, StopKind.NORMAL)

        yield events()


class ExplodingStreamClient(FakeLLMClient):
    """Yields a valid prefix, then fails — the mid-stream failure case."""

    @asynccontextmanager
    async def stream(self, system: str, user: str, max_tokens: int):  # type: ignore[no-untyped-def]
        self.calls.append((system, user, max_tokens))

        async def events():  # type: ignore[no-untyped-def]
            yield TextChunk("ANSWERED\nPartial answer ")
            raise ConnectionError("provider dropped the connection")

        yield events()


async def test_idle_stream_emits_a_comment_frame() -> None:
    """Time-to-first-frame is bounded below by the model's first newline, and
    thinking runs before it — so a healthy stream can be silent for seconds."""
    app = build_app(
        StubRetriever(),
        SlowStreamClient([f"ANSWERED\n{BODY}"], pause=0.08),
        sse_heartbeat_seconds=0.01,
    )
    _, frames = await stream(app)
    assert any(frame.startswith(":") for frame in frames)
    # Heartbeats do not disturb the event sequence.
    events = data_payloads(frames)
    assert events[0]["type"] == "verdict"
    assert events[-1]["type"] == "complete"


async def test_failure_after_the_first_frame_is_an_in_band_error_event() -> None:
    """Headers already went out with a 200; the status can no longer change, so
    the failure has to be in-band."""
    app = build_app(client=ExplodingStreamClient())
    response, frames = await stream(app)
    events = data_payloads(frames)

    assert response.status_code == 200  # cannot be retracted mid-stream
    assert events[0]["type"] == "verdict"
    assert events[-1]["type"] == "error"
    assert [event for event in events if event["type"] == "complete"] == []


# --- the terminal error frame carries its own rendering (KD-16 amendment 6) ----


def test_the_terminal_error_frame_carries_presentation_and_reset() -> None:
    """The one path where a client could not read the rendering off the wire.

    Headers went out with a 200 and cannot be retracted (KD-3), so a mid-stream
    failure arrives only as this frame. Before this it carried `code` and
    `message` alone — which left a frontend two options, both bad: render every
    mid-stream failure generically, or keep a local `code` → rendering map, which
    is the second list `conditions.py` exists to make impossible. The fields come
    from `spec_for`, the same source `envelope()` uses, so the two renderings of
    one condition cannot disagree.
    """
    import json

    from rag_qa.api.conditions import spec_for
    from rag_qa.api.sse import error_frame

    frame = error_frame("upstream_error", "provider unreachable")
    payload = json.loads(frame.removeprefix("data: "))
    spec = spec_for("upstream_error")

    assert payload["type"] == "error"
    assert payload["code"] == "upstream_error"
    assert payload["presentation"] == str(spec.presentation)
    assert payload["reset"] == str(spec.reset)


def test_every_condition_renders_the_same_in_a_frame_as_in_an_envelope() -> None:
    """Asserted across the whole registry rather than on one code: the failure
    this prevents is a *second* source of truth appearing, and one example cannot
    show that two sources agree everywhere."""
    import json

    from rag_qa.api.conditions import CONDITIONS
    from rag_qa.api.errors import ApiError, envelope
    from rag_qa.api.sse import error_frame

    for code in CONDITIONS:
        frame = json.loads(error_frame(code, "x").removeprefix("data: "))
        error = ApiError("x")
        error.code = code
        _, body, _ = envelope(error, "req-1")
        assert frame["presentation"] == body["error"]["presentation"], code
        assert frame["reset"] == body["error"]["reset"], code


async def test_a_stream_failure_delivers_the_rendering_over_the_wire() -> None:
    """End to end, not at the framing helper: a client reading the byte stream
    receives the fields it renders from."""
    import json

    from api_harness import build_app, sse_frames
    from test_api_budget import FailingStreamClient

    app = build_app(client=FailingStreamClient())
    _, frames = await sse_frames(app, {"question": "What applies?", "stream": True})

    errors = [
        json.loads(f.removeprefix("data: "))
        for f in frames
        if f.startswith("data: ") and '"type": "error"' in f
    ]
    assert len(errors) == 1
    assert errors[0]["presentation"] == "transient"  # upstream_error
    assert errors[0]["reset"] == "shortly"
