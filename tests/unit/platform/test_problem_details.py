"""Problem Details models and the exception handlers registered by ``create_app``."""

from collections.abc import AsyncIterator
from http import HTTPStatus

import httpx
import pydantic
import pytest
from fastapi import FastAPI, HTTPException
from structlog.testing import capture_logs

from tests.unit.platform.asgi import client_for
from yakhnama.main import (
    INTERNAL_ERROR_DETAIL,
    INVALID_DATA_DETAIL,
    create_app,
    status_for,
)
from yakhnama.modules.media.infrastructure.adapters.s3_storage import StorageError
from yakhnama.platform.auth.errors import IdentityProviderUnavailableError
from yakhnama.platform.problem_details import (
    PROBLEM_JSON_MEDIA_TYPE,
    PROBLEM_TYPE_BASE_URL,
    PROBLEM_TYPES,
    ProblemDetails,
    build_problem,
    problem_response,
    problem_type,
)
from yakhnama.platform.settings import Settings
from yakhnama.platform.tasks.errors import TaskQueueUnavailableError
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

REQUEST_ID = "req-123"


class HazardTypeMissingError(NotFoundError):
    """A module-style subclass that is not listed in the mapping.

    Implements: Domain Error.
    """


class LimitQuery(pydantic.BaseModel):
    """A model validated inside a route, outside request parsing.

    Implements: Query.

    Attributes:
        limit: Page size.
    """

    limit: int = pydantic.Field(le=200)


ERROR_CASES: list[tuple[type[YakhnamaError], int, str]] = [
    (NotFoundError, 404, "not-found"),
    (ConflictError, 409, "conflict"),
    (ValidationError, 422, "validation-error"),
    (PermissionDeniedError, 403, "permission-denied"),
    (PreconditionFailedError, 412, "precondition-failed"),
    (PreconditionRequiredError, 428, "precondition-required"),
    (AuthenticationError, 401, "authentication-failed"),
    (InvariantViolationError, 409, "invariant-violation"),
    (InvalidTransitionError, 409, "invalid-transition"),
    (IdentityProviderUnavailableError, 503, "service-unavailable"),
    (StorageError, 503, "service-unavailable"),
    (TaskQueueUnavailableError, 503, "service-unavailable"),
    (HazardTypeMissingError, 404, "not-found"),
]


@pytest.fixture
def raising_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    async def raise_error(kind: str) -> None:
        error_class = {case.__name__: case for case, _, _ in ERROR_CASES}.get(
            kind, YakhnamaError
        )
        message = "the requested record is unavailable"
        raise error_class(message, details={"id": 7})

    async def validate_inside() -> None:
        LimitQuery.model_validate({"limit": "reporter-phone-0300"})

    async def parse_query(limit: int) -> int:
        return limit

    async def crash() -> None:
        message = "postgresql://user:secret@db/crash"
        raise RuntimeError(message)

    async def raise_http(status: int) -> None:
        raise HTTPException(status_code=status, detail={"not": "a string"})

    app.add_api_route("/raise/{kind}", raise_error)
    app.add_api_route("/validate", validate_inside)
    app.add_api_route("/parse", parse_query)
    app.add_api_route("/crash", crash)
    app.add_api_route("/http/{status}", raise_http)
    app.add_api_route("/only-get", parse_query, methods=["GET"])
    return app


@pytest.fixture
async def raising_client(raising_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(raising_app) as client:
        client.headers["X-Request-ID"] = REQUEST_ID
        yield client


def test_problem_type_registered_slug_returns_url() -> None:
    result = problem_type("not-found")

    assert result == "https://yakhnama.org/problems/not-found"


def test_problem_type_unknown_slug_raises_key_error() -> None:
    with pytest.raises(KeyError):
        problem_type("made-up")


def test_problem_types_slugs_are_kebab_case() -> None:
    slugs = list(PROBLEM_TYPES)

    assert all(slug == slug.lower() and " " not in slug for slug in slugs)
    assert all(problem_type(slug).startswith(PROBLEM_TYPE_BASE_URL) for slug in slugs)


def test_build_problem_uses_status_phrase_and_slug_url() -> None:
    problem = build_problem(
        HTTPStatus.CONFLICT, "conflict", detail="taken", instance="req-1"
    )

    assert problem.title == "Conflict"
    assert problem.status == 409
    assert problem.type == "https://yakhnama.org/problems/conflict"
    assert problem.instance == "req-1"


def test_problem_response_sets_media_type_headers_and_omits_none() -> None:
    problem = build_problem(HTTPStatus.TOO_MANY_REQUESTS, "rate-limited")

    response = problem_response(problem, {"Retry-After": "3"})

    assert response.media_type == PROBLEM_JSON_MEDIA_TYPE
    assert response.headers["retry-after"] == "3"
    assert b'"detail"' not in bytes(response.body)


@pytest.mark.parametrize("status", [399, 600])
def test_problem_details_status_outside_error_range_is_rejected(status: int) -> None:
    with pytest.raises(pydantic.ValidationError, match="status"):
        ProblemDetails(title="Bad", status=status)


@pytest.mark.parametrize(("error_class", "status", "slug"), ERROR_CASES)
def test_status_for_error_class_returns_mapped_status(
    error_class: type[YakhnamaError], status: int, slug: str
) -> None:
    del slug

    result = status_for(error_class("message"))

    assert result == status


def test_status_for_bare_yakhnama_error_returns_internal_server_error() -> None:
    result = status_for(YakhnamaError("message"))

    assert result is HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.parametrize(("error_class", "status", "slug"), ERROR_CASES)
async def test_route_raising_domain_error_returns_problem_details(
    raising_client: httpx.AsyncClient,
    error_class: type[YakhnamaError],
    status: int,
    slug: str,
) -> None:
    response = await raising_client.get(f"/raise/{error_class.__name__}")

    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert response.json() == {
        "type": f"https://yakhnama.org/problems/{slug}",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": "the requested record is unavailable",
        "instance": REQUEST_ID,
    }


async def test_authentication_error_response_carries_bearer_challenge(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.get("/raise/AuthenticationError")

    assert response.headers["www-authenticate"] == 'Bearer realm="yakhnama"'


async def test_non_authentication_error_response_has_no_challenge(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.get("/raise/PermissionDeniedError")

    assert "www-authenticate" not in response.headers


async def test_route_raising_bare_yakhnama_error_returns_500_without_message(
    raising_client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        response = await raising_client.get("/raise/unknown")

    assert response.status_code == 500
    assert response.json() == {
        "type": "https://yakhnama.org/problems/internal-error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": INTERNAL_ERROR_DETAIL,
        "instance": REQUEST_ID,
    }
    assert {"event": "unmapped_domain_error", "error_code": "yakhnama_error"} in [
        {key: log[key] for key in ("event", "error_code") if key in log} for log in logs
    ]


async def test_route_raising_pydantic_error_returns_422_without_echoing_input(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.get("/validate")

    body = response.json()
    assert response.status_code == 422
    assert body["type"] == "https://yakhnama.org/problems/validation-error"
    assert body["detail"] == INVALID_DATA_DETAIL
    assert body["instance"] == REQUEST_ID
    assert body["errors"] == [
        {"loc": ["limit"], "msg": body["errors"][0]["msg"], "type": "int_parsing"}
    ]
    assert "reporter-phone-0300" not in response.text


async def test_request_parsing_error_returns_problem_without_input(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.get("/parse", params={"limit": "0300-1234567"})

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert body["type"] == "https://yakhnama.org/problems/validation-error"
    assert body["errors"][0]["loc"] == ["query", "limit"]
    assert set(body["errors"][0]) == {"loc", "msg", "type"}
    assert "0300-1234567" not in response.text


async def test_unknown_route_returns_404_problem(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.get("/no/such/route")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert response.json() == {
        "type": "https://yakhnama.org/problems/not-found",
        "title": "Not Found",
        "status": 404,
        "instance": REQUEST_ID,
    }


async def test_wrong_method_returns_405_problem_with_allow_header(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.delete("/only-get")

    assert response.status_code == 405
    assert response.json()["type"] == "https://yakhnama.org/problems/method-not-allowed"
    assert response.headers["allow"] == "GET"


@pytest.mark.parametrize(
    ("status", "slug"), [(400, "bad-request"), (418, "http-error")]
)
async def test_http_exception_with_non_string_detail_hides_detail(
    raising_client: httpx.AsyncClient, status: int, slug: str
) -> None:
    response = await raising_client.get(f"/http/{status}")

    body = response.json()
    assert response.status_code == status
    assert body["type"] == f"https://yakhnama.org/problems/{slug}"
    assert "detail" not in body


async def test_unexpected_exception_returns_500_problem_and_logs_type_only(
    raising_client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        response = await raising_client.get("/crash")

    body = response.json()
    assert response.status_code == 500
    assert body == {
        "type": "https://yakhnama.org/problems/internal-error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": INTERNAL_ERROR_DETAIL,
        "instance": REQUEST_ID,
    }
    assert response.headers["x-request-id"] == REQUEST_ID
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "secret" not in response.text
    crash_logs = [log for log in logs if log.get("event") == "unhandled_exception"]
    assert crash_logs == [
        {
            "event": "unhandled_exception",
            "error_type": "RuntimeError",
            "request_id": REQUEST_ID,
            "log_level": "error",
        }
    ]
