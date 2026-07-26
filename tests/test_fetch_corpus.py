"""fetch_corpus tests from SPEC-003 AC-1 (mocked transport, no network)."""

from pathlib import Path

import httpx
import pytest
from scripts.fetch_corpus import (
    CELLAR_URL,
    EURLEX_URL,
    MIN_BYTES,
    USER_AGENT,
    fetch_edgar,
    fetch_eurlex,
)

BIG_BODY = b"x" * (MIN_BYTES + 1)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(
        transport=handler, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )


def test_edgar_request_carries_contact_user_agent(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, content=BIG_BODY)

    with _client(httpx.MockTransport(handler)) as client:
        fetch_edgar(client, tmp_path)

    assert len(seen) == 1
    assert "@" in seen[0]  # descriptive UA with contact info (EDGAR requirement)
    assert (tmp_path / "nvda-10k-2026.htm").stat().st_size > MIN_BYTES


def test_eurlex_challenge_falls_back_to_cellar(tmp_path: Path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url).startswith(EURLEX_URL[: EURLEX_URL.index("?")]):
            return httpx.Response(202, content=b"<script>window.awsWafCookie</script>")
        assert str(request.url).startswith(CELLAR_URL)
        assert request.headers["Accept-Language"] == "en"
        return httpx.Response(200, content=BIG_BODY)

    with _client(httpx.MockTransport(handler)) as client:
        fetch_eurlex(client, tmp_path)

    assert len(requested) == 2
    assert (tmp_path / "eu-ai-act-2024-1689.html").read_bytes() == BIG_BODY


def test_eurlex_manual_instructions_when_all_blocked(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, content=b"challenge")

    with _client(httpx.MockTransport(handler)) as client, pytest.raises(RuntimeError) as excinfo:
        fetch_eurlex(client, tmp_path)

    assert "Download manually" in str(excinfo.value)
    assert not (tmp_path / "eu-ai-act-2024-1689.html").exists()


def test_small_response_never_written(tmp_path: Path) -> None:
    """A challenge page can never silently pass as corpus (SPEC-003 AC-1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"tiny")

    with _client(httpx.MockTransport(handler)) as client, pytest.raises(RuntimeError):
        fetch_edgar(client, tmp_path)

    assert not (tmp_path / "nvda-10k-2026.htm").exists()
