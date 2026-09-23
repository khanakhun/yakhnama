"""Application factory and composition root.

``create_app`` is the only place in the code base that builds the FastAPI application.
It loads settings, configures logging, binds the infrastructure container
(``platform/container.py``), configures telemetry, registers routers and registers the
exception handlers that turn errors into RFC 9457 Problem Details. These handlers are
the one place where errors meet HTTP (``AGENTS.md`` §2.3). Keeping construction in one
function lets tests build an isolated app with their own settings and fakes.

Patterns: Composition Root, Dependency Injection.
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from http import HTTPStatus
from importlib.metadata import version
from types import MappingProxyType
from typing import Final

import pydantic
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from yakhnama.platform import health
from yakhnama.platform.container import Container, build_container
from yakhnama.platform.logging import configure_logging
from yakhnama.platform.problem_details import (
    PROBLEM_JSON_MEDIA_TYPE,
    ProblemDetails,
    ProblemFieldError,
)
from yakhnama.platform.settings import Settings, get_settings
from yakhnama.platform.telemetry import configure_telemetry
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    ValidationError,
    YakhnamaError,
)

API_PREFIX = "/api/v1"

# Looked up along the raised error's MRO, so a module's subclass (for example
# ``HazardTypeNotFoundError(NotFoundError)``) maps without being listed here.
ERROR_STATUSES: Final[Mapping[type[YakhnamaError], HTTPStatus]] = MappingProxyType(
    {
        NotFoundError: HTTPStatus.NOT_FOUND,
        ConflictError: HTTPStatus.CONFLICT,
        ValidationError: HTTPStatus.UNPROCESSABLE_CONTENT,
        PermissionDeniedError: HTTPStatus.FORBIDDEN,
        PreconditionFailedError: HTTPStatus.PRECONDITION_FAILED,
        InvariantViolationError: HTTPStatus.CONFLICT,
        InvalidTransitionError: HTTPStatus.CONFLICT,
    }
)
INVALID_DATA_DETAIL: Final = "The data is invalid; see 'errors'."


def status_for(error: YakhnamaError) -> HTTPStatus:
    """Return the HTTP status for a Yakhnama error.

    Args:
        error: Any ``YakhnamaError``.

    Returns:
        The status of the closest mapped ancestor class, or 500 for an error class
        with no mapped ancestor (a bare ``YakhnamaError``).
    """
    for error_class in type(error).__mro__:
        status = ERROR_STATUSES.get(error_class)
        if status is not None:
            return status
    return HTTPStatus.INTERNAL_SERVER_ERROR


def _problem_response(problem: ProblemDetails) -> JSONResponse:
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=problem.status,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
    )


async def handle_yakhnama_error(request: Request, error: YakhnamaError) -> JSONResponse:
    """Map a ``YakhnamaError`` to a Problem Details response.

    The error's ``message`` is shown as ``detail`` because the error contract makes it
    safe for clients; ``details`` are not echoed because they may contain input
    values. An unmapped error is a server bug, so its message is withheld.

    Args:
        request: The failed request (unused).
        error: The raised error.

    Returns:
        An ``application/problem+json`` response with the mapped status.
    """
    del request
    status = status_for(error)
    if status is HTTPStatus.INTERNAL_SERVER_ERROR:
        # Fetched per call, not held in a module global: with cache_logger_on_first_use
        # a global proxy keeps the processor chain it first saw, so a later
        # configure_logging (another app, a test) would never reach it.
        structlog.get_logger(__name__).error(
            "unmapped_domain_error", error_code=error.code
        )
        detail = None
    else:
        detail = error.message
    return _problem_response(
        ProblemDetails(title=status.phrase, status=status, detail=detail)
    )


async def handle_pydantic_validation_error(
    request: Request, error: pydantic.ValidationError
) -> JSONResponse:
    """Map a ``pydantic.ValidationError`` raised inside the application to HTTP 422.

    This covers models built in application code (commands, value objects), not
    request parsing, which FastAPI reports itself. Only each error's location and
    message are returned; the rejected input is never echoed back.

    Args:
        request: The failed request (unused).
        error: The raised validation error.

    Returns:
        An ``application/problem+json`` response with status 422.
    """
    del request
    status = HTTPStatus.UNPROCESSABLE_CONTENT
    errors = tuple(
        ProblemFieldError(loc=tuple(item["loc"]), msg=item["msg"])
        for item in error.errors(
            include_url=False, include_context=False, include_input=False
        )
    )
    return _problem_response(
        ProblemDetails(
            title=status.phrase,
            status=status,
            detail=INVALID_DATA_DETAIL,
            errors=errors,
        )
    )


def create_app(
    settings: Settings | None = None, container: Container | None = None
) -> FastAPI:
    """Build and wire the Yakhnama FastAPI application.

    Args:
        settings: Settings to use; when omitted they are loaded from the environment
            via ``get_settings``. Tests pass explicit settings for isolation.
        container: A prebuilt container, for tests that wire fakes or a test
            database. When omitted one is built from ``settings``; building it does
            no I/O, and the app disposes the engine of a container it built itself
            when it shuts down.

    Returns:
        The configured application, with its settings on ``app.state.settings`` and
        its container on ``app.state.container``.

    Raises:
        pydantic.ValidationError: If settings are loaded and the environment is invalid.
        ValidationError: If the telemetry settings ask for an exporter that is not
            installed.
    """
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)
    owns_container = container is None
    resolved_container = (
        container if container is not None else build_container(resolved_settings)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        telemetry = app.state.telemetry
        if telemetry is not None:
            telemetry.shutdown()
        if owns_container:
            await resolved_container.engine.dispose()

    docs_enabled = resolved_settings.docs_enabled
    app = FastAPI(
        title=resolved_settings.app_name,
        version=version("yakhnama"),
        openapi_url=f"{API_PREFIX}/openapi.json" if docs_enabled else None,
        docs_url=f"{API_PREFIX}/docs" if docs_enabled else None,
        redoc_url=f"{API_PREFIX}/redoc" if docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.container = resolved_container
    # The FastAPI instrumentation adds a middleware, which Starlette only accepts
    # before the app starts, so telemetry is configured here and not in the lifespan.
    app.state.telemetry = configure_telemetry(
        resolved_settings, app, resolved_container.engine
    )
    app.exception_handler(YakhnamaError)(handle_yakhnama_error)
    app.exception_handler(pydantic.ValidationError)(handle_pydantic_validation_error)
    app.include_router(health.router)
    return app
