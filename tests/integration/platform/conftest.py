"""Fixtures for the platform integration tests: schema, settings, container, Redis."""

from collections.abc import AsyncIterator, Iterator

import pytest
from pydantic import PostgresDsn
from redis.asyncio import Redis
from sqlalchemy import Column, MetaData, String, Table, Uuid
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.community.redis import RedisContainer
from testcontainers.core.config import testcontainers_config

from yakhnama.platform.container import Container, build_container
from yakhnama.platform.db import Base
from yakhnama.platform.idempotency.models import IDEMPOTENCY_TABLE_NAME
from yakhnama.platform.outbox.models import OUTBOX_TABLE_NAME
from yakhnama.platform.settings import Settings

# Same image as docker-compose.yml, so tests and development run the same Redis.
REDIS_IMAGE = "redis:7-alpine"

# A stand-in for an aggregate table. It lives on its own MetaData so that it never
# appears in Base.metadata, which Alembic compares against the migrations.
PROBE_METADATA = MetaData()
PROBE_ITEMS = Table(
    "uow_probe_items",
    PROBE_METADATA,
    Column("id", Uuid, primary_key=True),
    Column("note", String(50), nullable=False),
)


@pytest.fixture
async def schema(async_engine: AsyncEngine) -> AsyncIterator[None]:
    """Create the outbox and probe tables for one test and drop them afterwards.

    Alembic owns the real schema from T4 on; creating the two tables directly keeps
    these tests independent of migrations and leaves the database empty afterwards.
    """
    outbox = Base.metadata.tables[OUTBOX_TABLE_NAME]
    async with async_engine.begin() as connection:
        await connection.run_sync(outbox.create)
        await connection.run_sync(PROBE_ITEMS.create)

    yield

    async with async_engine.begin() as connection:
        await connection.run_sync(PROBE_ITEMS.drop)
        await connection.run_sync(outbox.drop)


@pytest.fixture
def database_settings(postgis_url: str) -> Settings:
    """Return test settings pointing at the session PostGIS container."""
    return Settings(
        _env_file=None,
        environment="test",
        log_format="console",
        database_url=PostgresDsn(postgis_url),
    )


@pytest.fixture
async def container(database_settings: Settings) -> AsyncIterator[Container]:
    """Yield the production wiring on the test database; the engine is disposed."""
    container = build_container(database_settings)

    yield container

    await container.engine.dispose()


@pytest.fixture
async def idempotency_schema(async_engine: AsyncEngine) -> AsyncIterator[None]:
    """Create the ``idempotency_keys`` table for one test and drop it afterwards.

    Created from the ORM model, not by a migration: the migration is written by the
    persistence-engineer (``0006_idempotency_keys``) and checked by ``alembic check``.
    """
    table = Base.metadata.tables[IDEMPOTENCY_TABLE_NAME]
    async with async_engine.begin() as connection:
        await connection.run_sync(table.create)

    yield

    async with async_engine.begin() as connection:
        await connection.run_sync(table.drop)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """Start Redis once per session and yield its URL."""
    # See postgis_url: Ryuk would need a registry pull, a network call.
    testcontainers_config.ryuk_disabled = True
    container = RedisContainer(REDIS_IMAGE)
    try:
        container.start()
        host = container.get_container_host_ip()
        port = container.get_exposed_port(container.port)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    """Yield a client on an empty Redis database, closed after the test."""
    client: Redis = Redis.from_url(redis_url)
    await client.flushdb()

    yield client

    await client.flushdb()
    await client.aclose()
