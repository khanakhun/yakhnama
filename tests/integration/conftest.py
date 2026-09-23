"""Integration fixtures: one real PostGIS container for the whole test session.

The container starts only when a test asks for ``postgis_url``, so integration tests
that need no database (the hook tests) do not pay for it. Tests that create tables
drop them again, so later tests (the migration round-trip in particular) still see an
empty database.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.config import testcontainers_config

# Same image as docker-compose.yml, so tests and development run the same PostGIS.
POSTGIS_IMAGE = "postgis/postgis:16-3.5"


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
