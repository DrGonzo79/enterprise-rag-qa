"""The condition registry: one list, two sides (SPEC-008 AC-9, SPEC-006 KD-16).

The failure this file exists to prevent is drift, not breakage: a new failure
mode added to the server-side signal with no client-facing rendering, or a
client-facing state nothing can ever produce. Neither shows up as a broken test
elsewhere — the first renders as a generic error to a visitor, the second is dead
weight nobody notices — so they are asserted directly.
"""

import ast
import inspect
import pathlib

import pytest

from api_harness import build_app, get, post
from rag_qa.api import errors
from rag_qa.api.conditions import CONDITIONS, REFUSALS, Presentation, Reset, spec_for
from rag_qa.api.errors import ApiError

SRC = pathlib.Path(errors.__file__).resolve().parent.parent


def _error_classes() -> list[type[ApiError]]:
    return [
        obj
        for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, ApiError) and obj is not ApiError
    ]


# --- neither side may grow alone ----------------------------------------------


def test_every_raisable_code_has_a_client_rendering() -> None:
    """The direction that matters most: a failure mode reaching a caller with no
    instruction for how to show it."""
    raisable = {cls.code for cls in _error_classes()} | {
        code for _, _, code in errors._TRANSLATIONS
    }
    raisable.add(ApiError.code)  # the bare-ApiError fallback path
    missing = raisable - set(CONDITIONS)
    assert not missing, f"codes with no client-facing rendering: {sorted(missing)}"


def test_every_registered_condition_is_reachable() -> None:
    """The other direction: a rendering for a condition nothing can produce is a
    frontend branch that can never be exercised or reviewed against reality."""
    raisable = {cls.code for cls in _error_classes()} | {
        code for _, _, code in errors._TRANSLATIONS
    }
    # Codes assigned dynamically by the exception handlers rather than by a class.
    handler_assigned = _codes_assigned_in(SRC / "api" / "app.py")
    unreachable = set(CONDITIONS) - raisable - handler_assigned - {ApiError.code}
    assert not unreachable, f"registered but unreachable: {sorted(unreachable)}"


def _codes_assigned_in(path: pathlib.Path) -> set[str]:
    """String literals assigned to `.code` — the handlers set it directly."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Attribute) and t.attr == "code"]
        if not targets:
            continue
        for value in ast.walk(node.value):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value.value)
    return found


def test_a_subclass_with_no_registry_entry_cannot_be_defined() -> None:
    """Enforced at class creation, not in review: the check runs at import."""
    with pytest.raises(KeyError, match="no entry in CONDITIONS"):

        class Invented(ApiError):
            status_code = 503
            code = "a_brand_new_failure_mode"


def test_a_subclass_disagreeing_with_the_registry_status_cannot_be_defined() -> None:
    with pytest.raises(TypeError, match="disagrees with CONDITIONS"):

        class Wrong(ApiError):
            status_code = 418
            code = "overloaded"


# --- every entry is complete and usable ---------------------------------------


@pytest.mark.parametrize("code", sorted(CONDITIONS))
def test_every_entry_is_renderable(code: str) -> None:
    spec = spec_for(code)
    assert spec.code == code
    assert 400 <= spec.status <= 599
    assert isinstance(spec.presentation, Presentation)
    assert isinstance(spec.reset, Reset)
    assert spec.public_message.strip()
    assert "$" not in spec.public_message, "public text must name no figures"


def test_a_window_reset_is_only_claimed_where_a_clock_exists() -> None:
    """`reset` is a kind rather than a nullable timestamp precisely so the
    no-clock case has a rendering. An operator condition promising a countdown
    would be a lie that expires into the same error."""
    for code in ("embedder_mismatch", "empty_corpus", "misconfigured"):
        assert spec_for(code).reset is Reset.OPERATOR

    # The budget is the one refusal with an honest clock — and it keeps it even
    # when the ceiling in force came from the burst cap, because the cap changes
    # which number the ceiling is, not when the window rolls over.
    assert spec_for("budget_exhausted").reset is Reset.WINDOW

    # Its sibling has no clock at all, and that is the whole reason it is a
    # separate code (KD-16 amendment 5): the money is committed rather than
    # spent, so it comes back when the answers in flight settle. `envelope()`
    # renders from the code alone, so sharing one would mean sharing the
    # midnight countdown — a `Retry-After` that expires into a served request or
    # into another refusal, depending on nothing the caller can see.
    assert spec_for("budget_pressure").reset is Reset.SHORTLY
    assert spec_for("budget_pressure").presentation is Presentation.TRANSIENT


def test_only_the_budget_gets_the_explanatory_state() -> None:
    """KD-16 binds SPEC-009 to a specific panel for a specific reason — the demo
    is deliberately not answering. A transient overload is not that, and giving
    it the same panel would train a viewer to read "out of budget" as "broken"."""
    explanatory = {c for c, s in CONDITIONS.items() if s.presentation is Presentation.EXPLANATORY}
    assert explanatory == {"budget_exhausted"}


def test_refusals_are_the_ways_the_service_declines_work_it_would_do() -> None:
    """The server-side taxonomy SPEC-008's failure signal is built from — and it
    is the same object the client renderings hang off, which is the point."""
    assert set(REFUSALS) == {
        "budget_exhausted",
        "budget_pressure",
        "overloaded",
        "embedder_mismatch",
        "empty_corpus",
        "upstream_error",
    }
    # A bad request was never work the service agreed to do.
    assert "validation_error" not in REFUSALS
    assert "unauthenticated" not in REFUSALS


# --- the wire carries it ------------------------------------------------------


async def test_the_envelope_carries_presentation_and_reset() -> None:
    app = build_app()
    response = await post(app, "/query", {"question": "   "})
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["presentation"] == "request"
    assert error["reset"] == "none"


async def test_openapi_publishes_the_taxonomy() -> None:
    """SPEC-009 must be able to read the rendering off the schema rather than
    hardcoding a list that can fall behind this one."""
    app = build_app()
    document = (await get(app, "/openapi.json", key=None)).json()
    detail = document["components"]["schemas"]["ErrorDetail"]
    assert set(detail["properties"]) >= {"code", "message", "request_id", "presentation", "reset"}

    enums: set[str] = set()
    for schema in document["components"]["schemas"].values():
        if "enum" in schema:
            enums |= set(schema["enum"])
    assert {str(p) for p in Presentation} <= enums
    assert {str(r) for r in Reset} <= enums


async def test_the_failure_signal_is_labelled_from_the_same_registry() -> None:
    """The two sides meeting: SPEC-008's counter labels are registry codes, so a
    condition cannot be counted server-side without having a rendering."""
    from api_harness import ADMIN_KEY

    app = build_app()
    await post(app, "/query", {"question": "   "})
    body = (await get(app, "/metrics", key=ADMIN_KEY)).text
    labelled = {
        line.split('code="')[1].split('"')[0]
        for line in body.splitlines()
        if line.startswith("rag_qa_errors_total{")
    }
    assert labelled == {"validation_error"}
    assert labelled <= set(CONDITIONS)


def test_counting_an_unregistered_code_raises() -> None:
    """Enforced in the counter itself, not only at the raise site."""
    from rag_qa.api.metrics import Metrics

    with pytest.raises(KeyError, match="no entry in CONDITIONS"):
        Metrics().observe_error("invented_failure")


async def test_the_schema_tells_clients_how_to_fail_on_an_unknown_member() -> None:
    """`presentation` and `reset` are a published contract now, so a client that
    switches exhaustively breaks the day a member is added — on the error path,
    where it is least tested. The fallback is stated where a client reads it."""
    app = build_app()
    document = (await get(app, "/openapi.json", key=None)).json()
    detail = document["components"]["schemas"]["ErrorDetail"]["properties"]
    assert "degraded" in detail["presentation"]["description"]
    assert "unrecognised" in detail["presentation"]["description"]
    assert "shortly" in detail["reset"]["description"]
    assert "unrecognised" in detail["reset"]["description"]
