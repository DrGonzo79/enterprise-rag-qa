"""SPEC-001 Key decision 6: entrypoints load .env for local runs, real
environment variables take precedence."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SYNTH_EURLEX
from rag_qa.env import load_env


def _stripped_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "OPENAI_API_KEY")}


def test_cli_starts_with_only_dotenv(tmp_path: Path) -> None:
    """The CLI reaches the database (not KeyError) when DATABASE_URL exists
    only in a .env next to the working directory. The port is unreachable on
    purpose: a connection-refused naming it proves the value came from .env."""
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://rag:rag@127.0.0.1:59999/rag\n"
        "OPENAI_API_KEY=dummy-local-key\n"
    )
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "synth-eurlex.html").write_text(SYNTH_EURLEX, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "rag_qa.ingest", str(corpus)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_stripped_env(),
        timeout=120,
    )
    assert result.returncode != 0
    assert "KeyError" not in result.stderr
    assert "59999" in result.stderr or "refused" in result.stderr.lower()


def test_real_env_var_wins_over_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=from-dotenv\nLOG_LEVEL=DEBUG\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "from-real-environment")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    load_env()

    assert os.environ["DATABASE_URL"] == "from-real-environment"  # env wins
    assert os.environ["LOG_LEVEL"] == "DEBUG"  # .env fills the gap


def test_load_env_is_a_noop_without_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "unchanged")
    load_env()
    assert os.environ["DATABASE_URL"] == "unchanged"


# --- flags that only look like flags (KD-16 amendment 8) ----------------------


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_a_falsey_spelling_does_not_enable_anonymous_access(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bool(os.environ.get(...))` was the implementation, and every non-empty
    string is truthy — so `RAG_QA_ALLOW_ANONYMOUS=false`, which an operator
    writes precisely to say *no*, turned authentication off. A flag whose "off"
    spellings all mean "on" is worse than no flag: it reads as protection in the
    file where someone went looking to confirm it."""
    from rag_qa.api.deps import Settings

    monkeypatch.setenv("RAG_QA_ALLOW_ANONYMOUS", value)
    assert Settings.from_env().allow_anonymous is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_a_truthy_spelling_does_enable_it(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_qa.api.deps import Settings

    monkeypatch.setenv("RAG_QA_ALLOW_ANONYMOUS", value)
    assert Settings.from_env().allow_anonymous is True


def test_an_unrecognised_flag_value_refuses_to_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guessing which side of a security switch `=maybe` belongs on is exactly
    the decision a process should decline to make."""
    from rag_qa.api.deps import ConfigurationError, Settings

    monkeypatch.setenv("RAG_QA_ALLOW_ANONYMOUS", "maybe")
    with pytest.raises(ConfigurationError, match="not a boolean"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("RAG_QA_MAX_CONCURRENT_QUERIES", "four", "not an integer"),
        ("RAG_QA_SSE_HEARTBEAT_SECONDS", "soon", "not a number"),
        ("RAG_QA_MONTHLY_BUDGET_USD", "twenty", "not a decimal"),
    ],
)
def test_a_present_but_unparseable_value_names_itself(
    name: str, value: str, match: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absence defaults; a value that is present and unparseable does not.
    Defaulting past it silently substitutes a number the operator did not
    choose — and two of these three feed a spend ceiling."""
    from rag_qa.api.deps import ConfigurationError, Settings

    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=match):
        Settings.from_env()
