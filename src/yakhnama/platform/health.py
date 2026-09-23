"""Health endpoints used by orchestrators and load balancers.

``/health/live`` says the process can answer HTTP; ``/health/ready`` says its
dependencies answer too. Readiness checks only the database in Phase 1; the object
storage check joins in Phase 3, when the storage port exists.

``/health/ready`` is not rate-limited (a throttled probe would take a healthy
process out of service); instead its result is cached for ``READINESS_CACHE_TTL``
by ``ReadinessCache``, so however often it is called, the database sees at most
one probe query per second per process (ADR 0017).

Patterns: API Schema (proposed in ADR 0011), Dependency Injection, Decorator
(``ReadinessCache``).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Final, Literal

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from yakhnama.platform.container import Container, get_container
from yakhnama.shared_kernel.clock import Clock

CheckStatus = Literal["ok", "failed"]
READINESS_CACHE_TTL: Final = timedelta(seconds=1)

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


class ReadinessCache:
    """Caches the database check result for a short time.

    Concurrent callers during a refresh wait on one lock, so a burst of probes
    results in a single query.

    Implements: Decorator (caching around ``check_database``).
    """

    def __init__(self, clock: Clock, ttl: timedelta = READINESS_CACHE_TTL) -> None:
        """Create an empty cache.

        Args:
            clock: Source of the current instant.
            ttl: How long a result is reused.
        """
        self._clock = clock
        self._ttl = ttl
        self._lock = asyncio.Lock()
        self._result: CheckStatus | None = None
        self._checked_at: datetime | None = None

    async def get(self, check: Callable[[], Awaitable[CheckStatus]]) -> CheckStatus:
        """Return the cached result, running ``check`` when it is stale.

        Args:
            check: The dependency check to run on a miss.

        Returns:
            The fresh or cached result.
        """
        async with self._lock:
            now = self._clock.now()
            if (
                self._result is None
                or self._checked_at is None
                or now - self._checked_at >= self._ttl
            ):
                self._result = await check()
                self._checked_at = now
            return self._result


def get_readiness_cache(request: Request) -> ReadinessCache:
    """Return the application's readiness cache (a FastAPI dependency).

    Args:
        request: The current request.

    Returns:
        The cache stored on ``request.app.state.readiness_cache``.

    Raises:
        RuntimeError: If the application was not built by ``create_app``.
    """
    cache = getattr(request.app.state, "readiness_cache", None)
    if not isinstance(cache, ReadinessCache):
        message = "app.state.readiness_cache is missing; build the app with create_app"
        raise RuntimeError(message)
    return cache


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
    container: Annotated[Container, Depends(get_container)],
    cache: Annotated[ReadinessCache, Depends(get_readiness_cache)],
    response: Response,
) -> ReadinessResponse:
    """Report whether the dependencies needed to serve requests are reachable.

    Args:
        container: The application's container, for the engine and the timeout.
        cache: Reuses a result younger than ``READINESS_CACHE_TTL``.
        response: Used to set HTTP 503 when a check fails.

    Returns:
        The overall status and each check's outcome; HTTP 200 when every check
        passed, HTTP 503 otherwise.
    """
    database = await cache.get(
        lambda: check_database(
            container.engine, container.settings.health_ready_timeout_seconds
        )
    )
    if database == "ok":
        return ReadinessResponse(status="ok", checks=ReadinessChecks(database=database))
    response.status_code = HTTPStatus.SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="degraded", checks=ReadinessChecks(database=database)
    )
