"""HTTP-level tests for the health endpoints."""

import httpx


async def test_health_live_get_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_openapi_document_lists_health_live_under_health_tag(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    operation = response.json()["paths"]["/health/live"]["get"]
    assert operation["tags"] == ["health"]


async def test_unknown_route_get_returns_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404


async def test_openapi_document_lists_health_ready_with_503_response(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/openapi.json")

    operation = response.json()["paths"]["/health/ready"]["get"]
    assert operation["tags"] == ["health"]
    assert set(operation["responses"]) >= {"200", "503"}


async def test_health_ready_schema_names_status_and_database_check(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/openapi.json")

    schemas = response.json()["components"]["schemas"]
    assert schemas["ReadinessResponse"]["properties"]["status"]["enum"] == [
        "ok",
        "degraded",
    ]
    assert schemas["ReadinessChecks"]["properties"]["database"]["enum"] == [
        "ok",
        "failed",
    ]
