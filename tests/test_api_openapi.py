"""OpenAPI accuracy and supersession (SPEC-006 AC-11, AC-13).

Structural assertions, never a snapshot: a snapshot fails on every intentional
change, which trains people to regenerate it without reading the diff.
"""

import inspect
from typing import Any

from api_harness import build_app, get, post

PUBLIC_PATHS = {"/health", "/healthz"}
SECURED_PATHS = {"/query", "/ingest", "/metrics"}


async def document() -> dict[str, Any]:
    return (await get(build_app(), "/openapi.json", key=None)).json()


async def test_docs_and_schema_are_served() -> None:
    app = build_app()
    assert (await get(app, "/docs", key=None)).status_code == 200
    assert (await get(app, "/openapi.json", key=None)).status_code == 200


async def test_every_2xx_response_has_a_real_schema() -> None:
    """`/ask` returned a bare dict, so its entry documented an untyped object —
    coverage that says nothing is worse than none."""
    spec = await document()
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                if not str(status).startswith("2"):
                    continue
                content = response.get("content", {})
                assert content, f"{method.upper()} {path} {status} has no content"
                for media, entry in content.items():
                    schema = entry.get("schema", {})
                    assert schema, f"{method.upper()} {path} {media} has no schema"
                    assert schema != {"type": "object"}, (
                        f"{method.upper()} {path} {media} documents a bare object"
                    )


async def test_security_scheme_is_declared_exactly_where_auth_is_required() -> None:
    spec = await document()
    assert "APIKeyHeader" in spec["components"]["securitySchemes"]
    scheme = spec["components"]["securitySchemes"]["APIKeyHeader"]
    assert scheme["in"] == "header"
    assert scheme["name"] == "X-API-Key"

    for path in SECURED_PATHS:
        operations = spec["paths"][path]
        operation = next(iter(operations.values()))
        assert operation.get("security"), f"{path} does not declare the scheme"

    for path in PUBLIC_PATHS:
        operation = next(iter(spec["paths"][path].values()))
        assert not operation.get("security"), f"{path} must not require a key"


async def test_query_documents_both_response_content_types() -> None:
    spec = await document()
    content = spec["paths"]["/query"]["post"]["responses"]["200"]["content"]
    assert "application/json" in content
    assert "text/event-stream" in content


async def test_every_verdict_value_appears_in_the_response_schema() -> None:
    spec = await document()
    schema = spec["components"]["schemas"]["QueryResponse"]["properties"]["verdict"]
    values = set(schema.get("enum", []))
    assert values == {
        "answered",
        "insufficient_evidence",
        "truncated",
        "provider_refused",
        "error",
    }


async def test_error_responses_are_modelled() -> None:
    spec = await document()
    assert "ErrorResponse" in spec["components"]["schemas"]
    query_responses = spec["paths"]["/query"]["post"]["responses"]
    for status in ("401", "403", "422", "503"):
        ref = query_responses[status]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("ErrorResponse")


# --- AC-13: supersession is complete ------------------------------------------


async def test_ask_is_gone_and_not_aliased() -> None:
    app = build_app()
    assert (await post(app, "/ask", {"question": "What applies?"})).status_code == 404
    assert "/ask" not in (await document())["paths"]


def test_generation_api_module_no_longer_exists() -> None:
    import importlib

    try:
        importlib.import_module("rag_qa.generation.api")
    except ModuleNotFoundError:
        return
    raise AssertionError("rag_qa.generation.api should have been removed by SPEC-006")


def test_main_constructs_nothing_at_import_time() -> None:
    """The defect is *when* the work happens, which no request can observe — so
    this is asserted on the source (KD-11)."""
    from rag_qa import main

    source = inspect.getsource(main)
    assert "create_engine" not in source
    assert "_wire_ask" not in source
    assert "app = create_app()" in source
