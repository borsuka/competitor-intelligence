"""Liveness and readiness.

Separate endpoints on purpose: an orchestrator restarting a pod because Redis is briefly
unreachable makes an outage worse, so liveness answers "is this process alive" and only
readiness answers "can it serve traffic".
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])
log = get_logger(__name__)

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", version=VERSION, environment=settings.environment)


@router.get("/health/ready", response_model=HealthResponse)
async def ready(response: Response, session: SessionDep) -> HealthResponse:
    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = "unavailable"
        log.warning("health.database_unavailable", error=str(exc)[:200])

    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = "unavailable"
        log.warning("health.redis_unavailable", error=str(exc)[:200])

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=VERSION,
        environment=settings.environment,
        checks=checks,
    )


__all__ = ["VERSION", "router"]
