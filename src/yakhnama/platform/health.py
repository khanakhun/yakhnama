"""Health endpoints used by orchestrators and load balancers.

``/health/live`` says the process can answer HTTP; ``/health/ready`` says its
dependencies answer too. Readiness checks only the database in Phase 1; the object
storage check joins in Phase 3, when the storage port exists.

Patterns: API Schema (proposed in ADR 0011), Dependency Injection.
"""

import asyncio
from http import HTTPStatus
from typing import Annotated, Final, Literal

import structlog
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from yakhnama.platform.container import Container, get_container

CheckStatus = Literal["ok", "failed"]

_PROBE_STATEMENT: Final = text("SELECT 1")

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    """Body returned by the liveness probe.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        status: Always ``"ok"`` when the process can serve requests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]


@router.get(
    "/health/live",
    summary="Liveness probe",
    response_model=LivenessResponse,
)
async def read_liveness() -> LivenessResponse:
    """Report that the process is running and able to answer HTTP requests.

    The probe deliberately checks no dependency: a database outage must not make an
    orchestrator restart healthy application processes.

    Returns:
        A response whose ``status`` is ``"ok"``.
    """
    return LivenessResponse(status="ok")


class ReadinessChecks(BaseModel):
    """Outcome of each dependency check.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        database: ``"ok"`` if ``SELECT 1`` succeeded within the timeout.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    database: CheckStatus


class ReadinessResponse(BaseModel):
    """Body returned by the readiness probe.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        status: ``"ok"`` if every check passed, otherwise ``"degraded"``.
        checks: The individual check results.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "degraded"]
    checks: ReadinessChecks


async def check_database(engine: AsyncEngine, timeout_seconds: float) -> CheckStatus:
    """Run ``SELECT 1`` on a pooled connection within ``timeout_seconds``.

    A failure is logged with the error type only: driver messages can contain the
    host, user or DSN, which must not reach logs or clients.

    Args:
        engine: The application's engine.
        timeout_seconds: Upper bound for connecting and running the statement.

    Returns:
        ``"ok"`` or ``"failed"``.
    """
    try:
        async with asyncio.timeout(timeout_seconds), engine.connect() as connection:
            await connection.execute(_PROBE_STATEMENT)
    # Connection refused and DNS failures surface as OSError from asyncpg; server
    # errors as SQLAlchemyError; a hung server as TimeoutError.
    except (TimeoutError, OSError, SQLAlchemyError) as error:
        # Fetched per call, not held in a module global: with cache_logger_on_first_use
        # a global proxy keeps the processor chain it first saw, so a later
        # configure_logging (another app, a test) would never reach it.
        structlog.get_logger(__name__).warning(
            "readiness_check_failed", check="database", error_type=type(error).__name__
        )
        return "failed"
    return "ok"


@router.get(
    "/health/ready",
    summary="Readiness probe",
    response_model=ReadinessResponse,
    responses={
        HTTPStatus.SERVICE_UNAVAILABLE.value: {
            "model": ReadinessResponse,
            "description": "At least one dependency check failed.",
        }
    },
)
async def read_readiness(
    container: Annotated[Container, Depends(get_container)], response: Response
) -> ReadinessResponse:
    """Report whether the dependencies needed to serve requests are reachable.

    Args:
        container: The application's container, for the engine and the timeout.
        response: Used to set HTTP 503 when a check fails.

    Returns:
        The overall status and each check's outcome; HTTP 200 when every check
        passed, HTTP 503 otherwise.
    """
    database = await check_database(
        container.engine, container.settings.health_ready_timeout_seconds
    )
    if database == "ok":
        return ReadinessResponse(status="ok", checks=ReadinessChecks(database=database))
    response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="degraded", checks=ReadinessChecks(database=database)
    )
