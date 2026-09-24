"""Integration fixtures: real PostGIS and MinIO containers for the whole session.

Each container starts only when a test asks for it (``postgis_url``,
``minio_server``), so integration tests that need neither (the hook tests) do not
pay for them. Tests that create tables
drop them again, so later tests (the migration round-trip in particular) still see an
empty database.
"""

import time
from collections.abc import AsyncIterator, Iterator
from typing import Final

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.config import testcontainers_config
from testcontainers.core.container import DockerContainer

# Same image as docker-compose.yml, so tests and development run the same PostGIS.
POSTGIS_IMAGE = "postgis/postgis:16-3.5"

# Same image, pinned by digest, as the minio service in docker-compose.yml.
MINIO_IMAGE: Final = (
    "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
    "@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"
)
MINIO_PORT: Final = 9000
MINIO_TEST_USER: Final = "yakhnama-test"
MINIO_TEST_PASSWORD: Final = "yakhnama-test-only"  # noqa: S105  # reason: throwaway test container credential
MINIO_READY_TIMEOUT_SECONDS: Final = 60.0


class MinioServer(BaseModel):
    """Where the session MinIO container listens and how to sign in.

    Implements: Value Object.

    Attributes:
        endpoint_url: ``http://<host>:<port>`` of the S3 API.
        access_key_id: The root user.
        secret_access_key: The root password.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_url: str
    access_key_id: str
    secret_access_key: SecretStr


@pytest.fixture(scope="session")
def postgis_url() -> Iterator[str]:
    """Start PostGIS once per session and yield its asyncpg DSN."""
    # The Ryuk reaper would have to be pulled from a registry, a network call these
    # tests must not make; the container is stopped explicitly below instead.
    testcontainers_config.ryuk_disabled = True
    container = PostgresContainer(
        POSTGIS_IMAGE,
        username="yakhnama",
        password="yakhnama-test-only",  # noqa: S106  # reason: throwaway test container credential
        dbname="yakhnama",
        driver="asyncpg",
    )
    try:
        container.start()
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture
async def async_engine(postgis_url: str) -> AsyncIterator[AsyncEngine]:
    """Yield an engine on the session database, disposed after the test.

    Function-scoped because asyncpg connections belong to the event loop that opened
    them, and pytest-asyncio gives every test its own loop.
    """
    engine = create_async_engine(postgis_url)

    yield engine

    await engine.dispose()


def _wait_until_minio_is_ready(endpoint_url: str) -> None:
    # MinIO answers its readiness probe once it can serve S3 requests; polling it
    # is more reliable than matching a log line that changes between releases.
    deadline = time.monotonic() + MINIO_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{endpoint_url}/minio/health/ready", timeout=2.0)
        except httpx.TransportError:
            response = None
        if response is not None and response.status_code == httpx.codes.OK:
            return
        time.sleep(0.25)
    message = "MinIO did not become ready in time"
    raise TimeoutError(message)


@pytest.fixture(scope="session")
def minio_server() -> Iterator[MinioServer]:
    """Start MinIO once per session and yield its endpoint and credentials."""
    # See postgis_url: Ryuk would need a registry pull, a network call.
    testcontainers_config.ryuk_disabled = True
    container = (
        DockerContainer(MINIO_IMAGE)
        .with_command("server /data")
        .with_env("MINIO_ROOT_USER", MINIO_TEST_USER)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_TEST_PASSWORD)
        .with_exposed_ports(MINIO_PORT)
    )
    try:
        container.start()
        host = container.get_container_host_ip()
        port = container.get_exposed_port(MINIO_PORT)
        endpoint_url = f"http://{host}:{port}"
        _wait_until_minio_is_ready(endpoint_url)
        yield MinioServer(
            endpoint_url=endpoint_url,
            access_key_id=MINIO_TEST_USER,
            secret_access_key=SecretStr(MINIO_TEST_PASSWORD),
        )
    finally:
        container.stop()
