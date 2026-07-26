"""Provider-adapter tests from SPEC-005 AC-2 and AC-6(c).

Both adapters are exercised against synthesized provider-shaped responses and
must normalize to the same `LLMResult`. No network.
"""

import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import fields
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from rag_qa.generation.clients.base import LLMResult, StopKind, TextChunk, Usage
from rag_qa.generation.pricing import PRICING, Rate

OPENAI_TEST_IDENTITY = "openai:test-model"


@pytest.fixture
def openai_rate() -> Iterator[None]:
    """OpenAI ships no rate rows (KD-10), so tests register their own."""
    PRICING[OPENAI_TEST_IDENTITY] = (
        Rate(
            input_per_mtok=Decimal("2"),
            output_per_mtok=Decimal("10"),
            effective_from=date(2025, 1, 1),
            effective_until=None,
            verified_on=date(2026, 7, 26),
            source="https://example.invalid/pricing",
        ),
    )
    yield
    del PRICING[OPENAI_TEST_IDENTITY]


# --- fake provider SDKs --------------------------------------------------------


class _RefusalContent(list[Any]):
    def __iter__(self):  # type: ignore[override]
        raise AssertionError("content must not be read when stop_reason is 'refusal'")


def _fake_anthropic_module(response: Any, stream_events: list[Any] | None = None) -> Any:
    class FakeMessages:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def create(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return response

        @asynccontextmanager
        async def stream(self, **kwargs: Any) -> AsyncIterator[Any]:
            self.kwargs = kwargs

            class _Stream:
                async def __aiter__(self) -> AsyncIterator[Any]:
                    for event in stream_events or []:
                        yield event

            yield _Stream()

    class FakeAsyncAnthropic:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.messages = FakeMessages()

    return SimpleNamespace(AsyncAnthropic=FakeAsyncAnthropic)


def _install(monkeypatch: pytest.MonkeyPatch, name: str, module: Any) -> None:
    monkeypatch.setitem(sys.modules, name, module)


# --- AC-2: both adapters normalize to the same shape ---------------------------


async def test_anthropic_normalizes_usage_and_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="ANSWERED\nBody [1].")],
        usage=SimpleNamespace(input_tokens=1234, output_tokens=56),
    )
    _install(monkeypatch, "anthropic", _fake_anthropic_module(response))

    from rag_qa.generation.clients.anthropic import AnthropicClient

    client = AnthropicClient()
    result = await client.complete("sys", "user", 4096)

    assert client.identity == "anthropic:claude-sonnet-5"
    assert client.provider == "anthropic"
    # Anthropic's input_tokens/output_tokens land on the normalized names.
    assert result.prompt_tokens == 1234
    assert result.completion_tokens == 56
    assert result.text == "ANSWERED\nBody [1]."
    assert result.stop is StopKind.NORMAL


async def test_openai_normalizes_usage_and_text(
    monkeypatch: pytest.MonkeyPatch, openai_rate: None
) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ANSWERED\nBody [1]."),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1234, completion_tokens=56),
    )

    class FakeCompletions:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        async def create(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return response

    class FakeAsyncOpenAI:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    _install(monkeypatch, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    from rag_qa.generation.clients.openai import OpenAIClient

    client = OpenAIClient(model="test-model")
    result = await client.complete("sys", "user", 4096)

    assert result.prompt_tokens == 1234
    assert result.completion_tokens == 56
    assert result.text == "ANSWERED\nBody [1]."
    assert result.stop is StopKind.NORMAL
    # The asymmetry KD-4 records: OpenAI still accepts temperature.
    assert client._client.chat.completions.kwargs["temperature"] == 0


def test_llm_result_is_the_only_shape_callers_see() -> None:
    """AC-2 structurally: the normalized result carries no provider-specific
    field, so a swap changes nothing at the call site."""
    assert {f.name for f in fields(LLMResult)} == {
        "text",
        "prompt_tokens",
        "completion_tokens",
        "stop",
    }


def test_generation_package_imports_no_provider_sdk() -> None:
    """Provider SDKs are imported lazily inside each client's __init__, so
    importing the generation package never requires a provider installed."""
    import inspect

    import rag_qa.generation as package

    source = inspect.getsource(package)
    assert "import anthropic" not in source
    assert "import openai" not in source


# --- AC-6(c): a classifier refusal must not read content -----------------------


async def test_anthropic_refusal_never_reads_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 with stop_reason 'refusal' carries empty or partial content; an
    unguarded content[0] raises. The fake asserts the guard is real."""
    response = SimpleNamespace(
        stop_reason="refusal",
        content=_RefusalContent(),
        usage=SimpleNamespace(input_tokens=900, output_tokens=0),
    )
    _install(monkeypatch, "anthropic", _fake_anthropic_module(response))

    from rag_qa.generation.clients.anthropic import AnthropicClient

    result = await AnthropicClient().complete("sys", "user", 4096)
    assert result.stop is StopKind.REFUSAL
    assert result.text == ""
    assert result.prompt_tokens == 900


async def test_anthropic_max_tokens_maps_to_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(type="text", text="ANSWERED\nHalf")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4096),
    )
    _install(monkeypatch, "anthropic", _fake_anthropic_module(response))

    from rag_qa.generation.clients.anthropic import AnthropicClient

    result = await AnthropicClient().complete("sys", "user", 4096)
    assert result.stop is StopKind.MAX_TOKENS


# --- streaming normalization ---------------------------------------------------


async def test_anthropic_stream_drops_thinking_and_yields_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=1000)),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="thinking_delta", thinking="hidden reasoning"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="ANSWERED\nBody [1]."),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=42),
        ),
    ]
    _install(monkeypatch, "anthropic", _fake_anthropic_module(None, events))

    from rag_qa.generation.clients.anthropic import AnthropicClient

    client = AnthropicClient()
    collected: list[TextChunk | Usage] = []
    async with client.stream("sys", "user", 4096) as stream:
        async for event in stream:
            collected.append(event)

    texts = [e.text for e in collected if isinstance(e, TextChunk)]
    usages = [e for e in collected if isinstance(e, Usage)]
    # thinking_delta must never reach the parser — it would corrupt marker and
    # verdict parsing (KD-6).
    assert texts == ["ANSWERED\nBody [1]."]
    assert "hidden reasoning" not in "".join(texts)
    assert len(usages) == 1
    assert (usages[0].prompt_tokens, usages[0].completion_tokens) == (1000, 42)
    assert usages[0].stop is StopKind.NORMAL


def test_openai_client_unpriced_by_default_raises() -> None:
    """No OpenAI rate rows ship, so construction raises until one is added and
    verified — the designed behavior for an unpriced model (KD-10)."""
    from rag_qa.generation.types import UnknownModelError

    with pytest.raises(UnknownModelError):
        from rag_qa.generation.clients.openai import OpenAIClient

        OpenAIClient(model="gpt-unpriced")
