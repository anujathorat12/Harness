from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from harness.api.v1 import schemas
from harness.core.config import settings
from harness.core.database import get_db
from harness.core.security import ADMIN, AUDITOR, OPERATOR, require_role
from harness.domain import models

router = APIRouter(prefix="/system", tags=["system"])


def _check_container_runtime() -> tuple[bool, str]:
    """Best-effort reachability check for the optional ContainerRuntime path.
    Never raises -- an unreachable Docker daemon is a normal, expected state
    (e.g. this service's own container doesn't have the host's Docker socket
    mounted by default -- see docker-compose.yml's commented-out volume) and
    must be reported, not treated as an error."""
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True, "Docker daemon reachable -- container-shape agents can run via ContainerRuntime."
    except Exception as e:  # noqa: BLE001 -- deliberately broad, this is a diagnostic probe only
        return False, (
            "Docker daemon not reachable from this process "
            f"({type(e).__name__}) -- container-shape agents will fail to start. "
            "ProcessRuntime and DeclarativeRuntime are unaffected."
        )


@router.get("/status", response_model=schemas.SystemStatus)
async def status(db: AsyncSession = Depends(get_db), _principal=Depends(require_role(ADMIN, OPERATOR, AUDITOR))):
    """Operational status for the admin UI. Deliberately never returns API
    key values or any other secret -- only counts, flags, and config that is
    already safe to see in this platform's own Swagger UI."""
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001
        database = "error"

    sessions_running_now = (
        await db.execute(select(func.count()).select_from(models.Session).where(models.Session.status == "running"))
    ).scalar_one()

    container_available, container_note = _check_container_runtime()

    return schemas.SystemStatus(
        environment=settings.environment,
        api_version=settings.api_version,
        database=database,
        sessions_running_now=sessions_running_now,
        max_concurrent_sessions_total=settings.max_concurrent_sessions_total,
        max_concurrent_sessions_per_agent=settings.max_concurrent_sessions_per_agent,
        container_runtime_available=container_available,
        container_runtime_note=container_note,
    )
