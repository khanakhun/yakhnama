"""``GET /health/ready`` against real PostGIS and against unreachable databases."""

import asyncio
import socket
import time
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import PostgresDsn
from structlog.testing import capture_logs

from yakhnama.main import create_app
from yakhnama.platform.settings import Settings

pytestmark = pytest.mark.integration

# Stands in for the password in the DSN; it must never reach the response or logs.
DSN_MARKER = "never-logged-marker"


def _unreachable_settings(port: int, timeout_seconds: float) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        log_format="console",
        database_url=PostgresDsn(
            f"postgresql+asyncpg://yakhnama:{DSN_MARKER}@127.0.0.1:{port}/yakhnama"
        ),
        health_ready_timeout_seconds=timeout_seconds,
    )


def _closed_port() -> int:
    # Bind to an ephemeral port and release it: nothing listens there afterwards.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def _get_ready(app: FastAPI) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        return await client.get("/health/ready")


@pytest.fixture
async def silent_server_port() -> AsyncIterator[int]:
    """Yield the port of a local server that accepts connections and never answers."""
    connections: list[asyncio.StreamWriter] = []

    async def hold_open(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(writer)

    server = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]

    yield port

    for writer in connections:
        writer.close()
    server.close()
    await server.wait_closed()


async def test_health_ready_with_reachable_database_returns_200_ok(
    database_settings: Settings,
) -> None:
    response = await _get_ready(create_app(database_settings))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


async def test_health_ready_with_refused_connection_returns_503_degraded() -> None:
    app = create_app(_unreachable_settings(_closed_port(), timeout_seconds=2.0))

    with capture_logs() as logs:
        response = await _get_ready(app)

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": "failed"}}
    assert DSN_MARKER not in response.text
    assert DSN_MARKER not in str(logs)
    # The request-logging middleware adds its own "http_request" line.
    assert [log["event"] for log in logs] == ["readiness_check_failed", "http_request"]


async def test_health_ready_with_silent_server_times_out_and_returns_503(
    silent_server_port: int,
) -> None:
    app = create_app(_unreachable_settings(silent_server_port, timeout_seconds=0.2))
    started = time.monotonic()

    with capture_logs() as logs:
        response = await _get_ready(app)

    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert elapsed < 2.0
    assert logs[0]["error_type"] == "TimeoutError"
