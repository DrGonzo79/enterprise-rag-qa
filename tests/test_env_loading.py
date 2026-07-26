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
