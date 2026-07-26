"""GET /healthz (liveness, SPEC-001) and GET /health (readiness, SPEC-006).

Kept separate deliberately (KD-2). `/healthz` is what docker-compose
health-checks and it must never touch the database: a container healthcheck that
depends on Postgres restarts a perfectly healthy API on a database blip.
"""

import time

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from rag_qa.api.deps import AppState
from rag_qa.api.schemas import CheckResult, HealthResponse, HealthzResponse

router = APIRouter()


@router.get("/healthz", response_model=HealthzResponse, summary="Liveness")
async def healthz() -> HealthzResponse:
    return HealthzResponse(status="ok")


@router.get("/health", response_model=HealthResponse, summary="Readiness")
async def health(request: Request, response: Response) -> HealthResponse:
    state: AppState = request.app.state.rag
    checks: dict[str, CheckResult] = {}

    checks["database"], checks["migrations"], checks["corpus"] = await _database_checks(state)
    checks["generator"] = _generator_check(state)

    if not checks["database"].ok:
        status = "unavailable"
    elif all(check.ok for check in checks.values()):
        status = "ok"
    else:
        status = "degraded"

    # degraded still serves; unavailable does not.
    response.status_code = 503 if status == "unavailable" else 200
    return HealthResponse(status=status, checks=checks)  # type: ignore[arg-type]


async def _database_checks(state: AppState) -> tuple[CheckResult, CheckResult, CheckResult]:
    if state.session_factory is None:
        unset = CheckResult(ok=False, detail="no session factory configured")
        return unset, unset, unset

    started = time.perf_counter()
    try:
        async with state.session_factory() as session:
            await session.execute(text("SELECT 1"))
            latency_ms = int((time.perf_counter() - started) * 1000)
            revision = (
                await session.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            chunks = (await session.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
            identities = (
                (await session.execute(text("SELECT DISTINCT embedding_model FROM chunks")))
                .scalars()
                .all()
            )
    except Exception as exc:
        failed = CheckResult(ok=False, detail=type(exc).__name__)
        return failed, failed, failed

    return (
        CheckResult(ok=True, latency_ms=latency_ms),
        CheckResult(ok=revision is not None, revision=revision),
        CheckResult(
            ok=chunks > 0,
            chunks=int(chunks),
            embedder_identity=identities[0] if len(identities) == 1 else None,
            detail=None if chunks else "no corpus ingested",
        ),
    )


def _generator_check(state: AppState) -> CheckResult:
    """Configuration only — a health probe never spends a token."""
    if state.generator is None:
        return CheckResult(ok=False, detail="generation is not configured")
    return CheckResult(ok=True, identity=state.generator.identity)
