"""The production seed wiring and ``python -m yakhnama.seed`` against real PostGIS.

``build_seed_handler`` wires the YAML reader over ``data/reference``, the three
module load handlers and the actor allow-list exactly as the command line does. The
seed runs twice through it, and once more as a subprocess with ``--dry-run``.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Final

import pytest
from pydantic import PostgresDsn
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.geography.infrastructure.orm import PlaceNameRow, PlaceRow
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
from yakhnama.modules.impacts.infrastructure.orm import ImpactMetricRow
from yakhnama.platform.container import build_container, build_seed_handler
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.settings import Settings
from yakhnama.seed.application import SeedReferenceData, SeedReport

pytestmark = pytest.mark.integration

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
REFERENCE_DIRECTORY: Final = REPOSITORY_ROOT / "data" / "reference"
COUNTED_TABLES: Final = (
    HazardTypeRow,
    ImpactMetricRow,
    PlaceRow,
    PlaceNameRow,
    OutboxMessage,
)
SUBPROCESS_TIMEOUT_SECONDS: Final = 120


async def _row_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with session_factory() as session:
        for model in COUNTED_TABLES:
            count = await session.scalar(select(func.count()).select_from(model))
            counts[model.__tablename__] = count or 0
    return counts


def _created(report: SeedReport) -> tuple[int, int, int]:
    return (
        len(report.hazard_types.created),
        len(report.impact_metrics.created),
        len(report.places.created),
    )


async def test_build_seed_handler_twice_keeps_counts_and_adds_no_outbox_rows(
    postgis_url: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    container = build_container(
        Settings(
            _env_file=None,
            environment="test",
            log_format="console",
            database_url=PostgresDsn(postgis_url),
            reference_data_dir=REFERENCE_DIRECTORY,
        )
    )
    actor_id = container.id_generator.new_id()
    handler = build_seed_handler(container, actor_id)
    command = SeedReferenceData(actor_id=actor_id)

    try:
        first = await handler(command)
        counts_after_first = await _row_counts(session_factory)
        second = await handler(command)
        counts_after_second = await _row_counts(session_factory)
    finally:
        await container.engine.dispose()

    assert all(created > 0 for created in _created(first))
    assert counts_after_first["hazard_types"] == len(first.hazard_types.created)
    assert counts_after_first["impact_metrics"] == len(first.impact_metrics.created)
    assert counts_after_first["places"] == len(first.places.created)
    assert counts_after_first["outbox_messages"] > 0
    assert second.is_unchanged
    assert _created(second) == (0, 0, 0)
    assert counts_after_second == counts_after_first


async def test_seed_module_dry_run_subprocess_exits_zero_and_writes_nothing(
    postgis_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    environment = {
        **os.environ,
        "YAKHNAMA_DATABASE_URL": postgis_url,
        "YAKHNAMA_LOG_FORMAT": "json",
        "YAKHNAMA_REFERENCE_DATA_DIR": str(REFERENCE_DIRECTORY),
    }

    # An empty working directory, so no developer .env file changes the settings.
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "yakhnama.seed",
        "--dry-run",
        cwd=tmp_path,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        process.communicate(), SUBPROCESS_TIMEOUT_SECONDS
    )
    counts = await _row_counts(session_factory)

    assert process.returncode == 0, stderr.decode()
    events = [json.loads(line) for line in stdout.decode().splitlines() if line]
    (completed,) = [event for event in events if event["event"] == "seed_completed"]
    assert completed["dry_run"] is True
    assert completed["hazard_types"]["created"] > 0
    assert completed["impact_metrics"]["created"] > 0
    assert completed["places"]["created"] > 0
    assert any(event["event"] == "seed_actor_generated" for event in events)
    assert set(counts.values()) == {0}
