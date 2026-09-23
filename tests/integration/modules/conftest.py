"""Fixtures for the module persistence tests: a migrated schema and SQLAlchemy UoWs.

Unlike the platform tests, these tests run against the schema the migrations build:
``migrated_schema`` upgrades the session database to ``head`` once for this package
and downgrades it to ``base`` (and drops Alembic's version table) when the package is
done, so the migration harness and the platform tests still find an empty database.
Every test starts from empty tables because ``session_factory`` truncates them after
it.
"""

import asyncio
from argparse import Namespace
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from alembic import command
from alembic.config import Config
from pydantic import PostgresDsn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.geography.infrastructure.uow import (
    SqlAlchemyGeographyUnitOfWork,
)
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.platform.db import create_engine, create_session_factory
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.settings import Settings
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
ALEMBIC_INI: Final = REPOSITORY_ROOT / "alembic.ini"
VERSION_TABLE: Final = "alembic_version"
MODULE_TABLES: Final = (
    "memberships",
    "users",
    "organizations",
    "idempotency_keys",
    "place_names",
    "places",
    "hazard_types",
    "impact_metrics",
    "outbox_messages",
)
CLOCK_START: Final = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
CLOCK_STEP: Final = timedelta(seconds=1)
ID_SEED: Final = 1010


def _alembic_config(database_url: str) -> Config:
    config = Config(ALEMBIC_INI, cmd_opts=Namespace(x=[f"database_url={database_url}"]))
    # env.py would otherwise call fileConfig and replace pytest's log handlers.
    config.attributes["configure_logger"] = False
    return config


async def _drop_version_table(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE IF EXISTS {VERSION_TABLE}"))
    await engine.dispose()


@pytest.fixture(scope="package")
def migrated_schema(postgis_url: str) -> Iterator[None]:
    """Upgrade the session database to head for this package, then empty it again.

    Synchronous on purpose: ``migrations/env.py`` runs its own event loop with
    ``asyncio.run``, which cannot start inside a running pytest-asyncio loop.
    """
    config = _alembic_config(postgis_url)
    command.upgrade(config, "head")

    yield

    command.downgrade(config, "base")
    asyncio.run(_drop_version_table(_engine_for(postgis_url)))


def _settings_for(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        log_format="console",
        database_url=PostgresDsn(database_url),
    )


def _engine_for(database_url: str) -> AsyncEngine:
    return create_engine(_settings_for(database_url))


@pytest.fixture
async def engine(migrated_schema: None, postgis_url: str) -> AsyncIterator[AsyncEngine]:
    """Yield the production-configured engine on the migrated database.

    Function-scoped because asyncpg connections belong to the event loop that opened
    them, and pytest-asyncio gives every test its own loop.
    """
    del migrated_schema
    production_engine = _engine_for(postgis_url)

    yield production_engine

    await production_engine.dispose()


@pytest.fixture
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield the production session factory; every module table is emptied after."""
    yield create_session_factory(engine)

    async with engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE {', '.join(MODULE_TABLES)} RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def clock() -> SteppingClock:
    """Return a clock that moves one second per reading."""
    return SteppingClock(CLOCK_START, CLOCK_STEP)


@pytest.fixture
def ids() -> SequentialIdGenerator:
    """Return a deterministic id generator."""
    return SequentialIdGenerator(seed=ID_SEED)


@pytest.fixture
def outbox_writer(clock: SteppingClock) -> OutboxWriter:
    """Return the production outbox writer on the test clock."""
    return OutboxWriter(clock)


@pytest.fixture
def geography_uow_factory(
    session_factory: async_sessionmaker[AsyncSession], outbox_writer: OutboxWriter
) -> SqlAlchemyUnitOfWorkFactory[SqlAlchemyGeographyUnitOfWork]:
    """Return the SQLAlchemy geography unit-of-work factory."""
    return SqlAlchemyUnitOfWorkFactory(
        SqlAlchemyGeographyUnitOfWork,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
    )


@pytest.fixture
def hazards_uow_factory(
    session_factory: async_sessionmaker[AsyncSession], outbox_writer: OutboxWriter
) -> SqlAlchemyUnitOfWorkFactory[SqlAlchemyHazardsUnitOfWork]:
    """Return the SQLAlchemy hazards unit-of-work factory."""
    return SqlAlchemyUnitOfWorkFactory(
        SqlAlchemyHazardsUnitOfWork,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
    )


@pytest.fixture
def impacts_uow_factory(
    session_factory: async_sessionmaker[AsyncSession], outbox_writer: OutboxWriter
) -> SqlAlchemyUnitOfWorkFactory[SqlAlchemyImpactsUnitOfWork]:
    """Return the SQLAlchemy impacts unit-of-work factory."""
    return SqlAlchemyUnitOfWorkFactory(
        SqlAlchemyImpactsUnitOfWork,
        session_factory=session_factory,
        outbox_writer=outbox_writer,
    )
