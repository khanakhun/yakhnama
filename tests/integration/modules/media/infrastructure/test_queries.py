"""The SQL media query service against real PostGIS."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.media import MediaAssetTestFactory
from yakhnama.modules.media.application.dto import MediaAssetRecord
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.infrastructure.queries import SqlAlchemyMediaQueryService
from yakhnama.modules.media.infrastructure.uow import SqlAlchemyMediaUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory

pytestmark = pytest.mark.integration

type MediaFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyMediaUnitOfWork]


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
