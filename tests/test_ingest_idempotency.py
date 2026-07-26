"""Cross-process idempotency (SPEC-003 AC-6, cross-run form).

The savepoint-rollback fixtures verify single-process behavior only — they
structurally cannot catch cross-run defects because everything they observe
is rolled back (SPEC-002 test plan, limitation note). This test runs the real
CLI twice in separate processes against a real scratch database and asserts
the second run reports "unchanged", makes zero embedding calls, and leaves
row counts identical.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import asyncpg

from conftest import ADMIN_URL, DATABASE_URL

SCRATCH_DB = "rag_idempotency_test"
SCRATCH_URL = DATABASE_URL.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
REPO_ROOT = Path(__file__).resolve().parent.parent


async def _recreate_scratch_db() -> None:
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
        await admin.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    finally:
        await admin.close()


async def _row_counts() -> tuple[int, int]:
    conn = await asyncpg.connect(SCRATCH_URL.replace("+asyncpg", ""))
    try:
        docs = await conn.fetchval("SELECT count(*) FROM documents")
        chunks = await conn.fetchval("SELECT count(*) FROM chunks")
        return docs, chunks
    finally:
        await conn.close()


def _run_cli(corpus: Path, call_log: Path) -> list[str]:
    """One full CLI invocation in a fresh process; returns per-doc verdicts."""
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    env["DATABASE_URL"] = SCRATCH_URL
    env["RAG_QA_FAKE_EMBEDDER_LOG"] = str(call_log)
    result = subprocess.run(
        [sys.executable, "-m", "rag_qa.ingest", str(corpus), "--embedder", "fake"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((corpus / "ingest-manifest.json").read_text())
    return [d["verdict"] for d in manifest["documents"]]


async def test_second_process_reports_unchanged(synth_corpus: Path, tmp_path: Path) -> None:
    await _recreate_scratch_db()

    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", SCRATCH_URL)
    await asyncio.to_thread(command.upgrade, config, "head")

    call_log = tmp_path / "embed-calls.log"

    verdicts_run1 = _run_cli(synth_corpus, call_log)
    assert verdicts_run1 == ["new"] * 3
    calls_after_run1 = len(call_log.read_text().splitlines())
    assert calls_after_run1 > 0
    counts_after_run1 = await _row_counts()
    assert counts_after_run1[0] == 3
    assert counts_after_run1[1] > 0

    verdicts_run2 = _run_cli(synth_corpus, call_log)
    assert verdicts_run2 == ["unchanged"] * 3
    calls_after_run2 = len(call_log.read_text().splitlines())
    assert calls_after_run2 == calls_after_run1  # zero embedding calls on run 2
    assert await _row_counts() == counts_after_run1  # row counts identical
