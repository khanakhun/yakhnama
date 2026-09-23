"""Engine settings from ``yakhnama.platform.db`` against real PostGIS."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from yakhnama.platform.container import Container

pytestmark = pytest.mark.integration


async def test_create_engine_sessions_run_in_utc(container: Container) -> None:
    async with container.engine.connect() as connection:
        timezone = await connection.scalar(text("SHOW timezone"))

    assert timezone == "UTC"


async def test_create_engine_timestamptz_values_come_back_aware_in_utc(
    container: Container,
) -> None:
    async with container.engine.connect() as connection:
        value = await connection.scalar(
            text("SELECT TIMESTAMPTZ '2026-09-23 13:30:00+05'")
        )

    assert value == datetime(2026, 9, 23, 8, 30, tzinfo=UTC)
    assert isinstance(value, datetime)
    assert value.utcoffset() is not None


async def test_postgis_extension_is_available(container: Container) -> None:
    async with container.engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        version = await connection.scalar(text("SELECT postgis_lib_version()"))
        await connection.rollback()

    assert isinstance(version, str)
    assert version.startswith("3.5")
