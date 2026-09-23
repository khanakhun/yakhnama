"""Fixtures for the platform integration tests: schema, settings and container."""

from collections.abc import AsyncIterator

import pytest
from pydantic import PostgresDsn
from sqlalchemy import Column, MetaData, String, Table, Uuid
from sqlalchemy.ext.asyncio import AsyncEngine

from yakhnama.platform.container import Container, build_container
from yakhnama.platform.db import Base
from yakhnama.platform.outbox.models import OUTBOX_TABLE_NAME
from yakhnama.platform.settings import Settings

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
