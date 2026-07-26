"""Tests derived from SPEC-001 acceptance criteria (written before implementation)."""

from pathlib import Path

import httpx
import yaml

import rag_qa
from rag_qa.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


async def test_healthz() -> None:
    """AC-2: the endpoint the api container health-checks."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_package_version() -> None:
    """AC-1: the package imports and exposes a version."""
    assert isinstance(rag_qa.__version__, str)
    assert rag_qa.__version__


def test_env_example_keys() -> None:
    lines = (REPO_ROOT / ".env.example").read_text().strip().splitlines()
    entries = dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))
    assert set(entries) == {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DATABASE_URL",
        "LOG_LEVEL",
        # SPEC-006: the API refuses to start without a key, so an example that
        # omitted these would send a first-time reader into a startup failure.
        "RAG_QA_API_KEY",
        "RAG_QA_ADMIN_API_KEY",
        # SPEC-006 KD-16 (review amendment 2): the monthly cap is the budget an
        # owner commits to; the daily ceiling is derived from it unless set.
        "RAG_QA_MONTHLY_BUDGET_USD",
        "RAG_QA_DAILY_BUDGET_USD",
    }
    for secret in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RAG_QA_API_KEY", "RAG_QA_ADMIN_API_KEY"):
        assert entries[secret] == "", secret


def test_compose_contract() -> None:
    """AC-2: both services defined with healthchecks; api waits on healthy postgres."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    assert set(services) >= {"api", "postgres"}
    assert services["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert "healthcheck" in services["api"]
    assert "healthcheck" in services["postgres"]
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_ci_step_order() -> None:
    """AC-3: one job, gate steps in order lint -> type-check -> test."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = workflow["jobs"]
    assert len(jobs) == 1
    steps = next(iter(jobs.values()))["steps"]
    names = [step.get("name", "") for step in steps]
    positions = [names.index("Lint"), names.index("Type-check"), names.index("Test")]
    assert positions == sorted(positions)


def test_license_mit() -> None:
    first_line = (REPO_ROOT / "LICENSE").read_text().splitlines()[0]
    assert "MIT License" in first_line
