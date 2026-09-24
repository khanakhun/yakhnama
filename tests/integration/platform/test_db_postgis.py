"""Engine settings and SQL tracing from ``yakhnama.platform`` against real PostGIS."""

from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from yakhnama.main import create_app
from yakhnama.platform.container import Container
from yakhnama.platform.settings import Settings
from yakhnama.platform.telemetry import OpenTelemetryAdapter

pytestmark = pytest.mark.integration


async def test_create_engine_sessions_run_in_utc(container: Container) -> None:
    async with container.engine.connect() as connection:
        timezone = await connection.scalar(text("SHOW timezone"))

    assert timezone == "UTC"


async def test_create_engine_sessions_search_only_the_public_schema(
    container: Container,
) -> None:
    async with container.session_factory() as session:
        search_path = await session.scalar(text("SHOW search_path"))

    assert search_path == "public"


async def test_create_engine_sessions_print_shortest_round_trip_floats(
    container: Container,
) -> None:
    async with container.session_factory() as session:
        digits = await session.scalar(text("SHOW extra_float_digits"))
        # 0.1 + 0.2 is 0.30000000000000004 in binary; with extra_float_digits 0
        # PostgreSQL would print 0.3 and SQL rounding would diverge from repr().
        rendered = await session.scalar(
            text("SELECT (0.1::float8 + 0.2::float8)::text")
        )

    assert digits == "1"
    assert rendered == repr(0.1 + 0.2)


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


async def test_create_engine_sql_spans_carry_placeholders_only_and_no_exception_text(
    database_settings: Settings, async_engine: AsyncEngine
) -> None:
    marker = "0300-5550123"
    async with async_engine.begin() as connection:
        await connection.execute(
            text("CREATE TABLE telemetry_probe (phone varchar(20) PRIMARY KEY)")
        )
    app = create_app(database_settings.model_copy(update={"otel_enabled": True}))
    adapter = app.state.telemetry
    assert isinstance(adapter, OpenTelemetryAdapter)
    exporter = InMemorySpanExporter()
    adapter.add_exporter(exporter, is_batched=False)
    engine = app.state.container.engine
    insert_phone = text("INSERT INTO telemetry_probe (phone) VALUES (:phone)")
    try:
        async with engine.begin() as connection:
            await connection.execute(insert_phone, {"phone": marker})
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(insert_phone, {"phone": marker})
    finally:
        adapter.shutdown()
        await engine.dispose()
        async with async_engine.begin() as connection:
            await connection.execute(text("DROP TABLE telemetry_probe"))

    spans = [
        span
        for span in exporter.get_finished_spans()
        if "telemetry_probe" in str((span.attributes or {}).get("db.statement", ""))
    ]
    exported = str(
        [(span.attributes, span.events, span.status.description) for span in spans]
    )
    assert len(spans) == 2
    assert spans[1].status.status_code is StatusCode.ERROR
    assert all(span.status.description is None for span in spans)
    assert marker not in exported
    assert "DETAIL" not in exported
    assert {(span.attributes or {})["db.statement"] for span in spans} == {
        "INSERT INTO telemetry_probe (phone) VALUES ($1)"
    }
    assert "/*" not in exported
