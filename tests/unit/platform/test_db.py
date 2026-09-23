"""Unit tests for ``yakhnama.platform.db`` (no database connection is opened)."""

from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.schema import CreateIndex, CreateTable

from yakhnama.platform.db import (
    NAMING_CONVENTION,
    Base,
    UtcDateTime,
    create_engine,
    create_session_factory,
)
from yakhnama.platform.settings import Settings


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(
        Settings(_env_file=None, database_pool_size=7, database_echo=True)
    )

    yield engine

    await engine.dispose()


def _ddl(metadata: MetaData, table_name: str) -> str:
    table = metadata.tables[table_name]
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]  # reason: SQLAlchemy's dialect factory is untyped
    statements = [str(CreateTable(table).compile(dialect=dialect))]
    statements += [
        str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes
    ]
    return "\n".join(statements)


def _convention_metadata() -> MetaData:
    # A separate MetaData with the same convention, so test tables never leak into
    # Base.metadata (which Alembic compares against the migrations).
    metadata = MetaData(naming_convention=dict(NAMING_CONVENTION))
    Table("places", metadata, Column("id", Integer, primary_key=True))
    Table(
        "place_names",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("place_id", ForeignKey("places.id"), index=True),
        Column("value", String(100), unique=True),
        Column("rank", Integer),
        CheckConstraint("rank >= 0", name="rank_non_negative"),
    )
    return metadata


def test_naming_convention_strings_match_write_migration_skill() -> None:
    convention = dict(NAMING_CONVENTION)

    assert convention == {
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    }


def test_base_metadata_uses_the_naming_convention() -> None:
    convention = dict(Base.metadata.naming_convention)

    assert convention == dict(NAMING_CONVENTION)


def test_naming_convention_produces_deterministic_constraint_names() -> None:
    metadata = _convention_metadata()

    ddl = _ddl(metadata, "place_names")

    assert "CONSTRAINT pk_place_names PRIMARY KEY" in ddl
    assert "CONSTRAINT fk_place_names_place_id_places FOREIGN KEY" in ddl
    assert "CONSTRAINT uq_place_names_value UNIQUE" in ddl
    assert "CONSTRAINT ck_place_names_rank_non_negative CHECK" in ddl
    assert "CREATE INDEX ix_place_names_place_id" in ddl


def test_base_type_annotation_map_uses_timezone_aware_timestamp_and_native_uuid() -> (
    None
):
    type_map = Base.registry.type_annotation_map

    timestamp = type_map[datetime]
    identifier = type_map[UUID]

    assert isinstance(timestamp, TIMESTAMP)
    assert timestamp.timezone is True
    assert isinstance(identifier, Uuid)
    assert identifier.as_uuid is True


def test_utc_datetime_is_timestamp_with_time_zone() -> None:
    column_type = UtcDateTime

    assert isinstance(column_type, TIMESTAMP)
    assert column_type.timezone is True


def test_create_engine_settings_select_asyncpg_pool_size_and_echo(
    engine: AsyncEngine,
) -> None:
    pool = engine.sync_engine.pool

    assert engine.url.drivername == "postgresql+asyncpg"
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.size() == 7
    assert engine.echo is True


def test_create_engine_always_hides_bound_parameters(engine: AsyncEngine) -> None:
    hides = engine.sync_engine.hide_parameters

    assert hides is True


def test_create_engine_url_matches_settings_database_url(
    engine: AsyncEngine,
) -> None:
    rendered = engine.url.render_as_string(hide_password=False)

    assert rendered == Settings(_env_file=None).database_url.unicode_string()


async def test_create_session_factory_disables_expire_on_commit(
    engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        is_expiring = session.sync_session.expire_on_commit
        bind = session.bind

    assert is_expiring is False
    assert bind is engine


def test_index_on_convention_metadata_uses_column_label() -> None:
    metadata = MetaData(naming_convention=dict(NAMING_CONVENTION))
    table = Table("events", metadata, Column("id", Integer, primary_key=True))
    Index(None, table.c.id)

    ddl = _ddl(metadata, "events")

    assert "CREATE INDEX ix_events_id" in ddl
