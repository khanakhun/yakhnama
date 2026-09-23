"""Problem Details mapping registered by ``create_app``, exercised through the app."""

from collections.abc import AsyncIterator
from http import HTTPStatus

import httpx
import pydantic
import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from yakhnama.main import INVALID_DATA_DETAIL, create_app, status_for
from yakhnama.platform.problem_details import PROBLEM_JSON_MEDIA_TYPE, ProblemDetails
from yakhnama.platform.settings import Settings
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


ERROR_CASES: list[tuple[type[YakhnamaError], int]] = [
    (NotFoundError, 404),
    (ConflictError, 409),
    (ValidationError, 422),
    (PermissionDeniedError, 403),
    (PreconditionFailedError, 412),
    (InvariantViolationError, 409),
    (InvalidTransitionError, 409),
    (HazardTypeMissingError, 404),
]


@pytest.fixture
def raising_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    async def raise_error(kind: str) -> None:
        error_class = {case.__name__: case for case, _ in ERROR_CASES}.get(
            kind, YakhnamaError
        )
        message = "the requested record is unavailable"
        raise error_class(message, details={"id": 7})

    async def validate_inside() -> None:
        LimitQuery.model_validate({"limit": "reporter-phone-0300"})

    async def parse_query(limit: int) -> int:
        return limit

    app.add_api_route("/raise/{kind}", raise_error)
    app.add_api_route("/validate", validate_inside)
    app.add_api_route("/parse", parse_query)
    return app


@pytest.fixture
async def raising_client(raising_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=raising_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.parametrize(("error_class", "status"), ERROR_CASES)
def test_status_for_error_class_returns_mapped_status(
    error_class: type[YakhnamaError], status: int
) -> None:
    result = status_for(error_class("message"))

    assert result == status


def test_status_for_bare_yakhnama_error_returns_internal_server_error() -> None:
    result = status_for(YakhnamaError("message"))

    assert result is HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.parametrize(("error_class", "status"), ERROR_CASES)
async def test_route_raising_domain_error_returns_problem_details(
    raising_client: httpx.AsyncClient, error_class: type[YakhnamaError], status: int
) -> None:
    response = await raising_client.get(f"/raise/{error_class.__name__}")

    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": "the requested record is unavailable",
    }


async def test_route_raising_bare_yakhnama_error_returns_500_without_detail(
    raising_client: httpx.AsyncClient,
) -> None:
    with capture_logs() as logs:
        response = await raising_client.get("/raise/unknown")

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
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
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert body["type"] == "about:blank"
    assert body["title"] == "Unprocessable Content"
    assert body["status"] == 422
    assert body["detail"] == INVALID_DATA_DETAIL
    assert [error["loc"] for error in body["errors"]] == [["limit"]]
    assert set(body["errors"][0]) == {"loc", "msg"}
    assert "reporter-phone-0300" not in response.text


async def test_request_parsing_error_keeps_fastapi_default_response(
    raising_client: httpx.AsyncClient,
) -> None:
    response = await raising_client.get("/parse", params={"limit": "many"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize("status", [399, 600])
def test_problem_details_status_outside_error_range_is_rejected(status: int) -> None:
    with pytest.raises(pydantic.ValidationError, match="status"):
        ProblemDetails(title="Bad", status=status)
