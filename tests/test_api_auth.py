"""API-key auth and startup configuration (SPEC-006 AC-2)."""

import inspect

import pytest

from api_harness import ADMIN_KEY, READ_KEY, build_app, get, post
from rag_qa.api import create_app
from rag_qa.api.deps import ConfigurationError, Settings


async def test_missing_and_unknown_keys_are_401() -> None:
    app = build_app()
    for key in (None, "not-the-key"):
        response = await post(app, "/query", {"question": "Anything?"}, key=key)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"


async def test_read_key_on_admin_routes_is_403_not_401() -> None:
    """401 says *authenticate*; 403 says *that will not help*. Collapsing them
    sends a caller into a retry loop that cannot succeed."""
    app = build_app()
    ingest = await post(app, "/ingest", {"dry_run": True}, key=READ_KEY)
    assert ingest.status_code == 403
    assert ingest.json()["error"]["code"] == "forbidden"

    metrics = await get(app, "/metrics", key=READ_KEY)
    assert metrics.status_code == 403


async def test_admin_key_is_a_superset_of_read() -> None:
    app = build_app()
    response = await post(app, "/query", {"question": "What applies?"}, key=ADMIN_KEY)
    assert response.status_code == 200


async def test_public_routes_need_no_key() -> None:
    app = build_app()
    for path in ("/healthz", "/health", "/openapi.json", "/docs"):
        response = await get(app, path, key=None)
        assert response.status_code in (200, 503), path


async def test_anonymous_mode_is_explicit_not_a_default() -> None:
    app = build_app(api_key=None, admin_api_key=None, allow_anonymous=True)
    assert (await post(app, "/query", {"question": "Anything?"}, key=None)).status_code == 200


def test_key_comparison_is_constant_time() -> None:
    """A plain `==` on a secret leaks its length and prefix through timing."""
    from rag_qa.api import auth

    source = inspect.getsource(auth)
    assert "hmac.compare_digest" in source
    assert "presented == " not in source


# --- startup configuration (Key decision 11) ----------------------------------


async def test_startup_fails_when_no_key_is_configured() -> None:
    """A service whose auth silently disables itself when a variable is unset is
    worse than one with no auth, because it looks protected."""
    app = create_app(settings=Settings(), retriever=object(), generator=object())  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError) as excinfo:
        async with app.router.lifespan_context(app):
            pass
    assert "RAG_QA_API_KEY" in str(excinfo.value)
    assert "RAG_QA_ALLOW_ANONYMOUS" in str(excinfo.value)


async def test_startup_names_the_missing_provider_variables() -> None:
    """The defect this replaces: a missing ANTHROPIC_API_KEY left /ask unmounted,
    so a misconfigured deployment answered 404 — which reads as 'wrong URL' and
    sends an operator to look at routing instead of configuration."""
    app = create_app(settings=Settings(api_key="k"))  # no retriever/generator injected
    with pytest.raises(ConfigurationError) as excinfo:
        async with app.router.lifespan_context(app):
            pass
    message = str(excinfo.value)
    assert "DATABASE_URL" in message
    assert "ANTHROPIC_API_KEY" in message
    assert "OPENAI_API_KEY" in message


async def test_query_route_exists_even_when_misconfigured() -> None:
    """Never a 404 for a configuration problem: the route is always mounted."""
    app = create_app(settings=Settings(api_key=READ_KEY))
    response = await post(app, "/query", {"question": "Anything?"})
    assert response.status_code != 404
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "misconfigured"


async def test_a_falsey_anonymous_flag_leaves_authentication_actually_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behavioural half of the `_bool` fix, asserted on a response rather
    than on a parsed setting.

    `bool("false")` is `True`, so `RAG_QA_ALLOW_ANONYMOUS=false` set
    `allow_anonymous=True` and `verify()` returned immediately — **the operator
    asked for authentication and got none**. The unit test asserts the parsed
    flag, which is an intermediate; this asserts the thing the operator cares
    about, which is that a keyless request is refused. Both directions of the
    consequence have been stated wrong at least once while the code was right,
    which is exactly why the outcome gets its own assertion.
    """
    from api_harness import StubRetriever
    from rag_qa.generation.service import Generator
    from test_generation_service import FakeLLMClient

    monkeypatch.setenv("RAG_QA_ALLOW_ANONYMOUS", "false")
    monkeypatch.setenv("RAG_QA_API_KEY", "a-real-key")
    monkeypatch.setenv("RAG_QA_MONTHLY_BUDGET_USD", "20.00")

    resolved = Settings.from_env()
    assert resolved.allow_anonymous is False
    app = create_app(
        settings=resolved,
        retriever=StubRetriever(),  # type: ignore[arg-type]
        generator=Generator(FakeLLMClient()),
    )

    refused = await post(app, "/query", {"question": "What applies?"}, key=None)
    assert refused.status_code == 401, "an operator who wrote =false was served anonymously"
    served = await post(app, "/query", {"question": "What applies?"}, key="a-real-key")
    assert served.status_code == 200
