"""The SQL media query service against real PostGIS, the moderators' queue included."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.media import MediaAssetTestFactory, stored_file
from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import SteppingClock
from yakhnama.modules.media.application.dto import MediaAssetRecord
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.value_objects import (
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
)
from yakhnama.modules.media.infrastructure.queries import (
    SqlAlchemyMediaQueryService,
    queue_statement,
)
from yakhnama.modules.media.infrastructure.uow import SqlAlchemyMediaUnitOfWork
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    PageRequest,
    encode_cursor,
)

pytestmark = pytest.mark.integration

type MediaFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyMediaUnitOfWork]
type ReportsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyReportsUnitOfWork]


@pytest.fixture
async def stored(media_uow_factory: MediaFactory) -> list[MediaAsset]:
    """Store three requested assets."""
    assets = [MediaAssetTestFactory.build() for _ in range(3)]
    async with media_uow_factory() as uow:
        for asset in assets:
            await uow.media_assets.add(asset)
        await uow.commit()
    return assets


async def test_media_query_service_get_asset_returns_record(
    session_factory: async_sessionmaker[AsyncSession], stored: list[MediaAsset]
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)

    record = await service.get_asset(stored[1].id)

    assert record == MediaAssetRecord.from_entity(stored[1])


async def test_media_query_service_get_unknown_asset_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)

    assert await service.get_asset(FACTORY_IDS.new_id()) is None


async def test_media_query_service_list_assets_keeps_caller_order_and_skips_missing(
    session_factory: async_sessionmaker[AsyncSession], stored: list[MediaAsset]
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)
    missing = FACTORY_IDS.new_id()
    requested = [stored[2].id, missing, stored[0].id, stored[2].id]

    records = await service.list_assets(requested)

    assert [record.id for record in records] == [
        stored[2].id,
        stored[0].id,
        stored[2].id,
    ]
    assert records[1] == MediaAssetRecord.from_entity(stored[0])


async def test_media_query_service_list_no_assets_returns_empty_tuple(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)

    assert await service.list_assets([]) == ()


# --------------------------------------------------------------------------- #
# The moderators' queue                                                       #
# --------------------------------------------------------------------------- #

QUEUE_CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)


def _completed(asset: MediaAsset) -> MediaAsset:
    clock = SteppingClock(QUEUE_CREATED + timedelta(days=1), timedelta(seconds=1))
    return asset.complete_upload(stored_file(), clock=clock, ids=FACTORY_IDS).state


def _rejected(asset: MediaAsset) -> MediaAsset:
    clock = SteppingClock(QUEUE_CREATED + timedelta(days=2), timedelta(seconds=1))
    scanned = asset.mark_scan(ScanStatus.CLEAN, clock=clock, ids=FACTORY_IDS).state
    return scanned.moderate(
        ModerationStatus.REJECTED,
        SensitivityFlag.NONE,
        "Test rejection",
        clock=clock,
        ids=FACTORY_IDS,
    ).state


@pytest.fixture
async def queued(media_uow_factory: MediaFactory) -> list[MediaAsset]:
    """Store four completed assets a minute apart (the third rejected).

    One more asset is only requested, so it never appears in the queue.
    """
    assets = [
        _completed(
            MediaAssetTestFactory.build(
                created_at=QUEUE_CREATED + timedelta(minutes=index)
            )
        )
        for index in range(4)
    ]
    assets[2] = _rejected(assets[2])
    requested = MediaAssetTestFactory.build(created_at=QUEUE_CREATED)
    async with media_uow_factory() as uow:
        for asset in (*assets, requested):
            await uow.media_assets.add(asset)
        await uow.commit()
    return assets


async def _whole_queue(
    service: SqlAlchemyMediaQueryService,
    limit: int,
    moderation_status: ModerationStatus | None = None,
    scan_status: ScanStatus | None = None,
) -> list[MediaAssetRecord]:
    records: list[MediaAssetRecord] = []
    cursor: str | None = None
    while True:
        page = await service.list_queue(
            moderation_status=moderation_status,
            scan_status=scan_status,
            page=PageRequest(limit=limit, cursor=cursor),
        )
        records.extend(page.items)
        if page.next_cursor is None:
            return records
        cursor = page.next_cursor


async def test_media_queue_pages_completed_assets_oldest_first(
    session_factory: async_sessionmaker[AsyncSession], queued: list[MediaAsset]
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)

    records = await _whole_queue(service, limit=3)

    assert [record.id for record in records] == [asset.id for asset in queued]
    assert records[0] == MediaAssetRecord.from_entity(queued[0])


async def test_media_queue_filters_by_moderation_and_scan_status(
    session_factory: async_sessionmaker[AsyncSession], queued: list[MediaAsset]
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)

    pending = await _whole_queue(
        service, limit=10, moderation_status=ModerationStatus.PENDING
    )
    clean = await _whole_queue(service, limit=10, scan_status=ScanStatus.CLEAN)

    assert [record.id for record in pending] == [
        queued[0].id,
        queued[1].id,
        queued[3].id,
    ]
    assert [record.id for record in clean] == [queued[2].id]


async def test_media_queue_resolves_the_newest_report_listing_the_asset(
    session_factory: async_sessionmaker[AsyncSession],
    reports_uow_factory: ReportsFactory,
    queued: list[MediaAsset],
) -> None:
    older = ReportTestFactory.build(
        media_ids=(queued[0].id,), created_at=QUEUE_CREATED + timedelta(hours=1)
    )
    newer = ReportTestFactory.build(
        media_ids=(queued[1].id, queued[0].id),
        created_at=QUEUE_CREATED + timedelta(hours=2),
    )
    async with reports_uow_factory() as uow:
        await uow.reports.add(older)
        await uow.reports.add(newer)
        await uow.commit()
    service = SqlAlchemyMediaQueryService(session_factory)

    records = await _whole_queue(service, limit=10)

    assert [record.report_id for record in records] == [newer.id, newer.id, None, None]
    stored = await service.get_asset(queued[0].id)
    assert stored is not None
    assert stored.report_id is None


async def test_media_queue_keeps_the_stored_report_id(
    session_factory: async_sessionmaker[AsyncSession],
    media_uow_factory: MediaFactory,
) -> None:
    report_id = FACTORY_IDS.new_id()
    asset = _completed(
        MediaAssetTestFactory.build(created_at=QUEUE_CREATED, report_id=report_id)
    )
    async with media_uow_factory() as uow:
        await uow.media_assets.add(asset)
        await uow.commit()
    service = SqlAlchemyMediaQueryService(session_factory)

    records = await _whole_queue(service, limit=10)

    assert [record.report_id for record in records] == [report_id]


@pytest.mark.parametrize(
    "sort_key", ["not-a-date", "2026-09-01T12:00:00"], ids=["garbage", "naive"]
)
async def test_media_queue_with_a_bad_cursor_sort_key_is_refused(
    session_factory: async_sessionmaker[AsyncSession], sort_key: str
) -> None:
    service = SqlAlchemyMediaQueryService(session_factory)
    cursor = encode_cursor(
        CursorPayload(sort_key=sort_key, last_id=FACTORY_IDS.new_id())
    )

    with pytest.raises(ValidationError):
        await service.list_queue(
            moderation_status=None,
            scan_status=None,
            page=PageRequest(limit=2, cursor=cursor),
        )


# Enough unrelated reports, newest first by ``created_at``, that walking
# ``ix_reports_created_at_id`` backwards looks cheap to the planner and is not.
_MANY_REPORTS: Final = (
    "INSERT INTO reports (id, reporter_id, source_id, observed_at, "
    "observed_at_precision, observation, description, original_language, "
    "media_ids, status, revision, submitted_at, version, created_at, updated_at, "
    "lineage_id) "
    "SELECT ids.id, ids.id, ids.id, now(), 'exact', "
    "ST_SetSRID(ST_MakePoint(74.3, 35.9), 4326), 'Synthetic.', 'en', "
    "jsonb_build_array(gen_random_uuid()::text), 'submitted', 1, now(), 1, "
    "now() - make_interval(secs => ids.series), now(), ids.id "
    "FROM (SELECT gen_random_uuid() AS id, series "
    "FROM generate_series(1, 20000) AS series) AS ids"
)


async def test_media_queue_report_lookup_uses_the_media_ids_gin_index(
    session_factory: async_sessionmaker[AsyncSession], queued: list[MediaAsset]
) -> None:
    del queued
    statement = queue_statement(
        moderation_status=None, scan_status=None, since=None, limit=51
    )
    async with session_factory() as session:
        compiled = statement.compile(
            dialect=session.get_bind().dialect, compile_kwargs={"literal_binds": True}
        )
        await session.execute(text(_MANY_REPORTS))
        await session.execute(text("ANALYZE reports"))
        await session.execute(text("ANALYZE media_assets"))
        plan = "\n".join(
            (await session.execute(text(f"EXPLAIN {compiled}"))).scalars().all()
        )
        await session.rollback()

    assert "ix_reports_media_ids_gin" in plan, plan
    assert "ix_reports_created_at_id" not in plan, plan
