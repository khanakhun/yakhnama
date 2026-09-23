"""Application factory and composition root.

``create_app`` is the only place in the code base that builds the FastAPI application.
It loads settings, configures logging, binds the infrastructure container
(``platform/container.py``), configures telemetry, installs the HTTP middlewares,
registers routers and registers the exception handlers that turn errors into RFC 9457
Problem Details. These handlers are the one place where errors meet HTTP
(``AGENTS.md`` §2.3). Keeping construction in one function lets tests build an
isolated app with their own settings and fakes.

Every Problem Details body carries ``type`` = ``https://yakhnama.org/problems/<slug>``
(the slugs are listed in ``platform/problem_details.py``) and ``instance`` = the
request id, which is also echoed in the ``X-Request-ID`` header and bound to every
log line of the request.

Patterns: Composition Root, Dependency Injection.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from http import HTTPStatus
from importlib.metadata import version
from types import MappingProxyType
from typing import Final

import pydantic
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from yakhnama.modules.events.api.router import (
    moderation_router as events_moderation_router,
)
from yakhnama.modules.events.api.router import router as events_router
from yakhnama.modules.geography.api.router import router as geography_router
from yakhnama.modules.hazards.api.router import router as hazards_router
from yakhnama.modules.identity.api.router import (
    moderation_router,
)
from yakhnama.modules.identity.api.router import router as identity_router
from yakhnama.modules.impacts.api.claims_router import (
    moderation_router as impact_claims_moderation_router,
)
from yakhnama.modules.impacts.api.claims_router import (
    router as impact_claims_router,
)
from yakhnama.modules.impacts.api.router import router as impacts_router
from yakhnama.modules.media.api.router import (
    moderation_router as media_moderation_router,
)
from yakhnama.modules.media.api.router import router as media_router
from yakhnama.modules.provenance.api.router import (
    moderation_router as provenance_moderation_router,
)
from yakhnama.modules.provenance.api.router import router as provenance_router
from yakhnama.modules.reports.api.router import router as reports_router
from yakhnama.modules.verification.api.router import (
    moderation_router as verification_moderation_router,
)
from yakhnama.platform import health
from yakhnama.platform.api_docs import build_api_reference
from yakhnama.platform.auth.errors import IdentityProviderUnavailableError
from yakhnama.platform.auth.resolution import PrincipalResolutionMiddleware
from yakhnama.platform.container import Container, build_container
from yakhnama.platform.http import (
    API_CONTENT_SECURITY_POLICY,
    RequestBodyGuardMiddleware,
    RequestIdMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
    build_cors_options,
    security_headers,
)
from yakhnama.platform.idempotency.middleware import IdempotencyMiddleware
from yakhnama.platform.logging import configure_logging
from yakhnama.platform.problem_details import (
    ProblemFieldError,
    build_problem,
    problem_response,
)
from yakhnama.platform.ratelimit.middleware import RateLimitMiddleware
from yakhnama.platform.request_state import get_request_id
from yakhnama.platform.settings import Settings, get_settings
from yakhnama.platform.telemetry import configure_telemetry
from yakhnama.shared_kernel.errors import (
    AuthenticationError,
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    PreconditionRequiredError,
    ValidationError,
    YakhnamaError,
)

API_PREFIX = "/api/v1"
DOCS_PATH: Final = f"{API_PREFIX}/docs"
OPENAPI_PATH: Final = f"{API_PREFIX}/openapi.json"
WWW_AUTHENTICATE: Final = "WWW-Authenticate"
# Responses about a person: never cacheable, even without an Authorization header.
PRIVATE_PATH_PREFIXES: Final = (f"{API_PREFIX}/me", f"{API_PREFIX}/users")
BEARER_CHALLENGE: Final = 'Bearer realm="yakhnama"'

# Looked up along the raised error's MRO, so a module's subclass (for example
# ``HazardTypeNotFoundError(NotFoundError)``) maps without being listed here. The
# slug comes from the mapped ancestor, so the problem types stay a closed list.
ERROR_STATUSES: Final[Mapping[type[YakhnamaError], tuple[HTTPStatus, str]]] = (
    MappingProxyType(
        {
            NotFoundError: (HTTPStatus.NOT_FOUND, "not-found"),
            ConflictError: (HTTPStatus.CONFLICT, "conflict"),
            ValidationError: (HTTPStatus.UNPROCESSABLE_CONTENT, "validation-error"),
            PermissionDeniedError: (HTTPStatus.FORBIDDEN, "permission-denied"),
            PreconditionFailedError: (
                HTTPStatus.PRECONDITION_FAILED,
                "precondition-failed",
            ),
            PreconditionRequiredError: (
                HTTPStatus.PRECONDITION_REQUIRED,
                "precondition-required",
            ),
            AuthenticationError: (HTTPStatus.UNAUTHORIZED, "authentication-failed"),
            InvariantViolationError: (HTTPStatus.CONFLICT, "invariant-violation"),
            InvalidTransitionError: (HTTPStatus.CONFLICT, "invalid-transition"),
            IdentityProviderUnavailableError: (
                HTTPStatus.SERVICE_UNAVAILABLE,
                "service-unavailable",
            ),
        }
    )
)
# Statuses Starlette raises itself (unknown route, wrong method, ...).
HTTP_ERROR_SLUGS: Final[Mapping[int, str]] = MappingProxyType(
    {
        HTTPStatus.BAD_REQUEST: "bad-request",
        HTTPStatus.UNAUTHORIZED: "authentication-failed",
        HTTPStatus.FORBIDDEN: "permission-denied",
        HTTPStatus.NOT_FOUND: "not-found",
        HTTPStatus.METHOD_NOT_ALLOWED: "method-not-allowed",
        HTTPStatus.CONTENT_TOO_LARGE: "payload-too-large",
    }
)
INTERNAL_ERROR: Final = (HTTPStatus.INTERNAL_SERVER_ERROR, "internal-error")
INVALID_DATA_DETAIL: Final = "The data is invalid; see 'errors'."
INTERNAL_ERROR_DETAIL: Final = (
    "An unexpected error occurred; quote the instance when reporting it."
)


def status_for(error: YakhnamaError) -> HTTPStatus:
    """Return the HTTP status for a Yakhnama error.

    Args:
        error: Any ``YakhnamaError``.

    Returns:
        The status of the closest mapped ancestor class, or 500 for an error class
        with no mapped ancestor (a bare ``YakhnamaError``).
    """
    return _mapping_for(error)[0]


def _mapping_for(error: YakhnamaError) -> tuple[HTTPStatus, str]:
    for error_class in type(error).__mro__:
        mapping = ERROR_STATUSES.get(error_class)
        if mapping is not None:
            return mapping
    return INTERNAL_ERROR


async def handle_yakhnama_error(request: Request, error: YakhnamaError) -> JSONResponse:
    """Map a ``YakhnamaError`` to a Problem Details response.

    The error's ``message`` is shown as ``detail`` because the error contract makes it
    safe for clients; ``details`` are not echoed because they may contain input
    values. An unmapped error is a server bug, so its message is withheld. A 401
    carries ``WWW-Authenticate: Bearer`` (RFC 6750 §3).

    Args:
        request: The failed request.
        error: The raised error.

    Returns:
        An ``application/problem+json`` response with the mapped status.
    """
    status, slug = _mapping_for(error)
    if status is HTTPStatus.INTERNAL_SERVER_ERROR:
        # Fetched per call, not held in a module global: with cache_logger_on_first_use
        # a global proxy keeps the processor chain it first saw, so a later
        # configure_logging (another app, a test) would never reach it.
        structlog.get_logger(__name__).error(
            "unmapped_domain_error", error_code=error.code
        )
        detail = INTERNAL_ERROR_DETAIL
    else:
        detail = error.message
    headers = (
        {WWW_AUTHENTICATE: BEARER_CHALLENGE}
        if status is HTTPStatus.UNAUTHORIZED
        else None
    )
    return problem_response(
        build_problem(
            status, slug, detail=detail, instance=get_request_id(request.scope)
        ),
        headers,
    )


async def handle_pydantic_validation_error(
    request: Request, error: pydantic.ValidationError
) -> JSONResponse:
    """Map a ``pydantic.ValidationError`` raised inside the application to HTTP 422.

    This covers models built in application code (commands, value objects).
    Only each error's location, message and type are returned; the rejected input
    is never echoed back.

    Args:
        request: The failed request.
        error: The raised validation error.

    Returns:
        An ``application/problem+json`` response with status 422.
    """
    return _validation_problem(
        request,
        error.errors(include_url=False, include_context=False, include_input=False),
    )


async def handle_request_validation_error(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    """Map FastAPI's request parsing errors to HTTP 422 Problem Details.

    FastAPI's default body echoes each rejected ``input``, which can be a phone
    number or a location typed into the wrong field; only ``loc``, ``msg`` and
    ``type`` are kept.

    Args:
        request: The failed request.
        error: The parsing error.

    Returns:
        An ``application/problem+json`` response with status 422.
    """
    return _validation_problem(request, list(error.errors()))


def _validation_problem(
    request: Request, items: Sequence[Mapping[str, object]]
) -> JSONResponse:
    errors = tuple(
        ProblemFieldError(
            loc=tuple(_location_parts(item.get("loc"))),
            msg=str(item.get("msg", "")),
            type=str(item["type"]) if "type" in item else None,
        )
        for item in items
    )
    return problem_response(
        build_problem(
            HTTPStatus.UNPROCESSABLE_CONTENT,
            "validation-error",
            detail=INVALID_DATA_DETAIL,
            instance=get_request_id(request.scope),
            errors=errors,
        )
    )


def _location_parts(location: object) -> list[str | int]:
    if not isinstance(location, list | tuple):
        return []
    return [part if isinstance(part, int) else str(part) for part in location]


async def handle_http_exception(
    request: Request, error: StarletteHTTPException
) -> JSONResponse:
    """Map framework HTTP errors (unknown route, wrong method, ...) to Problem Details.

    Args:
        request: The failed request.
        error: The HTTP error Starlette or a route raised.

    Returns:
        An ``application/problem+json`` response with the error's status and
        headers (for example ``Allow`` on a 405).
    """
    status = HTTPStatus(error.status_code)
    # FastAPI's HTTPException allows any detail; only a plain, non-default string is
    # a client-safe explanation.
    detail = (
        error.detail
        if isinstance(error.detail, str) and error.detail != status.phrase
        else None
    )
    return problem_response(
        build_problem(
            status,
            HTTP_ERROR_SLUGS.get(status, "http-error"),
            detail=detail,
            instance=get_request_id(request.scope),
        ),
        error.headers,
    )


def build_internal_error_handler(
    settings: Settings,
) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    """Return the catch-all handler for unexpected exceptions (HTTP 500).

    Starlette runs it in ``ServerErrorMiddleware``, outside every other middleware,
    so the handler itself adds the request id and the security headers that those
    middlewares would otherwise have set.

    Args:
        settings: Supplies ``request_id_header`` and whether HSTS applies.

    Returns:
        The handler.
    """

    async def handle_unexpected_error(
        request: Request, error: Exception
    ) -> JSONResponse:
        request_id = get_request_id(request.scope)
        # The error type only: an exception message can quote data (a DSN, an
        # input value), and the traceback is for the operator's own tracing.
        structlog.get_logger(__name__).error(
            "unhandled_exception",
            error_type=type(error).__name__,
            request_id=request_id,
        )
        headers = security_headers(
            API_CONTENT_SECURITY_POLICY,
            is_hsts_enabled=settings.environment == "production",
        )
        if request_id is not None:
            headers[settings.request_id_header] = request_id
        status, slug = INTERNAL_ERROR
        return problem_response(
            build_problem(
                status, slug, detail=INTERNAL_ERROR_DETAIL, instance=request_id
            ),
            headers,
        )

    return handle_unexpected_error


def install_middlewares(
    app: FastAPI,
    settings: Settings,
    container: Container,
    page_policies: Mapping[str, str],
) -> None:
    """Install the platform middlewares in their required order.

    ``add_middleware`` puts each new middleware outside the previous ones, so they
    are added innermost first. The resulting order, outermost first, is: request id,
    request logging, security headers, trusted host, CORS, principal resolution,
    rate limit, body guard, idempotency; see ``platform/http.py`` for why.

    Args:
        app: The application.
        settings: The settings the middlewares are configured from.
        container: Supplies the validator, limiter, store, clock and ids.
        page_policies: CSPs of HTML pages, by path.
    """
    app.add_middleware(
        IdempotencyMiddleware,
        store=container.idempotency_store,
        clock=container.clock,
        ttl=timedelta(hours=settings.idempotency_ttl_hours),
        path_prefix=API_PREFIX,
    )
    app.add_middleware(
        RequestBodyGuardMiddleware, max_bytes=settings.max_request_body_bytes
    )
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            limiter=container.rate_limiter,
            anonymous_per_minute=settings.rate_limit_anonymous_per_minute,
            authenticated_per_minute=settings.rate_limit_authenticated_per_minute,
        )
    app.add_middleware(
        PrincipalResolutionMiddleware, validator=container.token_validator
    )
    cors_options = build_cors_options(settings)
    if cors_options is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_options.allow_origins,
            allow_methods=cors_options.allow_methods,
            allow_headers=cors_options.allow_headers,
            expose_headers=cors_options.expose_headers,
            allow_credentials=cors_options.allow_credentials,
            max_age=cors_options.max_age,
        )
    app.add_middleware(TrustedHostMiddleware, trusted_hosts=settings.trusted_hosts)
    app.add_middleware(
        SecurityHeadersMiddleware,
        is_hsts_enabled=settings.environment == "production",
        page_policies=page_policies,
        private_path_prefixes=PRIVATE_PATH_PREFIXES,
    )
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        RequestIdMiddleware,
        header_name=settings.request_id_header,
        id_generator=container.id_generator,
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
            no I/O, and the app closes a container it built itself when it shuts
            down.

    Returns:
        The configured application, with its settings on ``app.state.settings``,
        its container on ``app.state.container`` and the readiness cache on
        ``app.state.readiness_cache``.

    Raises:
        pydantic.ValidationError: If settings are loaded and the environment is invalid.
        ValidationError: If the telemetry settings ask for an exporter that is not
            installed.
    """
    resolved_settings = settings if settings is not None else get_settings()
    configure_logging(resolved_settings)
    if "environment" not in resolved_settings.model_fields_set:
        # A forgotten YAKHNAMA_ENVIRONMENT in a real deployment would silently run
        # with development defaults; say so loudly (ADR 0015, More information).
        structlog.get_logger(__name__).warning(
            "environment_defaulted", environment=resolved_settings.environment
        )
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
            await resolved_container.aclose()

    docs_enabled = resolved_settings.docs_enabled
    # Swagger UI and ReDoc are off for good: Scalar replaces both (ADR 0014).
    app = FastAPI(
        title=resolved_settings.app_name,
        version=version("yakhnama"),
        openapi_url=OPENAPI_PATH if docs_enabled else None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.container = resolved_container
    app.state.readiness_cache = health.ReadinessCache(resolved_container.clock)
    # The FastAPI instrumentation adds a middleware, which Starlette only accepts
    # before the app starts, so telemetry is configured here and not in the lifespan.
    app.state.telemetry = configure_telemetry(
        resolved_settings, app, resolved_container.engine
    )
    page_policies: dict[str, str] = {}
    if docs_enabled:
        page = build_api_reference(
            openapi_url=OPENAPI_PATH,
            title=resolved_settings.app_name,
            script_url=resolved_settings.docs_scalar_js_url,
        )
        page_policies[DOCS_PATH] = page.content_security_policy

        @app.get(DOCS_PATH, include_in_schema=False)
        async def read_api_reference() -> HTMLResponse:
            return HTMLResponse(page.html)

    install_middlewares(app, resolved_settings, resolved_container, page_policies)
    app.exception_handler(YakhnamaError)(handle_yakhnama_error)
    app.exception_handler(pydantic.ValidationError)(handle_pydantic_validation_error)
    app.exception_handler(RequestValidationError)(handle_request_validation_error)
    app.exception_handler(StarletteHTTPException)(handle_http_exception)
    app.exception_handler(Exception)(build_internal_error_handler(resolved_settings))
    app.include_router(health.router)
    app.include_router(hazards_router)
    app.include_router(impacts_router)
    app.include_router(geography_router)
    app.include_router(identity_router)
    app.include_router(moderation_router)
    app.include_router(provenance_router)
    app.include_router(reports_router)
    app.include_router(media_router)
    app.include_router(events_router)
    app.include_router(impact_claims_router)
    app.include_router(provenance_moderation_router)
    app.include_router(media_moderation_router)
    app.include_router(events_moderation_router)
    app.include_router(impact_claims_moderation_router)
    app.include_router(verification_moderation_router)
    return app
