"""Platform behaviour every module route inherits.

Rate limiting, request ids, NUL rejection, token expiry, the Problem Details shape
and the security declared in OpenAPI.
"""

from datetime import timedelta
from typing import Any, Final

import pytest

from tests.factories.hazards import HazardTypeTestFactory
from tests.fakes.api import auth_headers, build_test_app
from tests.fakes.auth import StaticRateLimiter
from yakhnama.main import OPENAPI_PATH

ANONYMOUS_OPERATIONS: Final = frozenset(
    {
        ("get", "/api/v1/hazard-types"),
        ("get", "/api/v1/hazard-types/{code}"),
        ("get", "/api/v1/impact-metrics"),
        ("get", "/api/v1/impact-metrics/{code}"),
        ("get", "/api/v1/places"),
        ("get", "/api/v1/places/{place_id}"),
        ("get", "/api/v1/organizations/{organization_id}"),
        # Phase 3: the public record and its provenance.
        ("get", "/api/v1/events"),
        ("get", "/api/v1/events/{event_id}"),
        ("get", "/api/v1/events/{event_id}/timeline"),
        ("get", "/api/v1/events/{event_id}/impacts"),
        ("get", "/api/v1/infrastructure-assets/{asset_id}"),
        ("get", "/api/v1/media/{asset_id}"),
        ("get", "/api/v1/sources"),
        ("get", "/api/v1/sources/{source_id}"),
        # Phase 4: the dataset catalog and the ingested open data.
        ("get", "/api/v1/datasets"),
        ("get", "/api/v1/datasets/{code}"),
        ("get", "/api/v1/datasets/{code}/runs"),
        ("get", "/api/v1/ingestion-runs/{run_id}"),
        ("get", "/api/v1/observations"),
        ("get", "/api/v1/raster-assets"),
    }
)


async def _openapi_operations() -> dict[tuple[str, str], dict[str, Any]]:
    api = build_test_app()
    async with api.client() as client:
        document = (await client.get(OPENAPI_PATH)).json()
    return {
        (method, path): operation
        for path, operations in document["paths"].items()
        if path.startswith("/api/v1/")
        for method, operation in operations.items()
    }


async def test_api_response_carries_rate_limit_headers() -> None:
    limiter = StaticRateLimiter()
    api = build_test_app(rate_limiter=limiter)

    async with api.client() as client:
        response = await client.get("/api/v1/hazard-types")

    assert response.headers["x-ratelimit-limit"] == "60"
    assert response.headers["x-ratelimit-remaining"] == "59"
    assert len(limiter.calls) == 1


async def test_api_request_over_limit_returns_429_problem_with_retry_after() -> None:
    api = build_test_app(
        rate_limiter=StaticRateLimiter(is_allowed=False, retry_after_seconds=9)
    )

    async with api.client() as client:
        response = await client.get("/api/v1/me", headers=auth_headers())

    assert response.status_code == 429
    assert response.headers["retry-after"] == "9"
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_api_request_id_sent_by_client_is_echoed_in_header_and_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get(
            "/api/v1/hazard-types/unknown_code",
            headers={"X-Request-ID": "client-request-0001"},
        )

    assert response.headers["x-request-id"] == "client-request-0001"
    assert response.json()["instance"] == "client-request-0001"


async def test_api_query_with_nul_character_returns_422_nul_problem() -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.get("/api/v1/places?q=a%00b")

    assert response.status_code == 422
    assert response.json()["type"].endswith("/nul-character")


async def test_api_expired_token_on_public_route_returns_401() -> None:
    api = build_test_app(hazard_types=[HazardTypeTestFactory.build(code="glof")])
    api.clock.advance(timedelta(hours=1))

    async with api.client() as client:
        response = await client.get("/api/v1/hazard-types", headers=auth_headers())

    assert response.status_code == 401


async def test_openapi_declares_bearer_auth_only_on_protected_operations() -> None:
    operations = await _openapi_operations()

    secured = {key for key, operation in operations.items() if "security" in operation}

    assert set(operations) - secured == ANONYMOUS_OPERATIONS
    assert all(operations[key]["security"] == [{"bearerAuth": []}] for key in secured)


async def test_openapi_documents_problem_details_for_validation_errors() -> None:
    operations = await _openapi_operations()

    documented = {
        key
        for key, operation in operations.items()
        if "application/problem+json"
        in operation["responses"].get("422", {}).get("content", {})
    }

    assert documented == set(operations)


@pytest.mark.parametrize(
    "path", ["/api/v1/hazard-types", "/api/v1/places?q=test", "/api/v1/me"]
)
async def test_api_unknown_method_returns_405_problem(path: str) -> None:
    api = build_test_app()

    async with api.client() as client:
        response = await client.put(path, json={})

    assert response.status_code == 405
    assert response.headers["content-type"].startswith("application/problem+json")
