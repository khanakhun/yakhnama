"""The SQLAlchemy media asset repository and media unit of work against real PostGIS."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.media import MediaAssetTestFactory, stored_file, synthetic_sha256
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.errors import MediaAssetNotFoundError
from yakhnama.modules.media.domain.value_objects import (
    ExifFacts,
    MimeType,
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
    public_object_key,
)
from yakhnama.modules.media.infrastructure.uow import SqlAlchemyMediaUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

pytestmark = pytest.mark.integration

type MediaFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyMediaUnitOfWork]

# Before the test clock's start, so every change the clock stamps is later.
CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)
EXIF: Final = ExifFacts(
    taken_at=DateWithPrecision(
        value=datetime(2026, 8, 30, 9, 15, tzinfo=UTC), precision=DatePrecision.EXACT
    ),
    location=Coordinates(longitude=74.63651234567891, latitude=36.31234567891234),
    camera="Test Camera 1",
)


def _completed(
    asset: MediaAsset,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
    *,
    sha256: str | None = None,
    exif: ExifFacts | None = EXIF,
) -> MediaAsset:
    stored = stored_file(sha256=sha256, mime_type=MimeType.PNG, exif=exif)
    return asset.complete_upload(stored, clock=clock, ids=ids).state


def _published(clock: SteppingClock, ids: SequentialIdGenerator) -> MediaAsset:
    asset = MediaAssetTestFactory.build(
        created_at=CREATED, report_id=FACTORY_IDS.new_id()
    )
    asset = _completed(asset, clock, ids)
    asset = asset.mark_scan(ScanStatus.CLEAN, clock=clock, ids=ids).state
    asset = asset.moderate(
        ModerationStatus.APPROVED,
        SensitivityFlag.NONE,
        "Test approval",
        clock=clock,
        ids=ids,
    ).state
    return asset.publish_public_copy(
        public_object_key(asset.id), clock=clock, ids=ids
    ).state


async def _store(factory: MediaFactory, *assets: MediaAsset) -> None:
    async with factory() as uow:
        for asset in assets:
            await uow.media_assets.add(asset)
        await uow.commit()


async def _get(factory: MediaFactory, asset: MediaAsset) -> MediaAsset | None:
    async with factory() as uow:
        return await uow.media_assets.get(asset.id)


async def test_media_repository_add_requested_asset_then_get_returns_equal_asset(
    media_uow_factory: MediaFactory,
) -> None:
    asset = MediaAssetTestFactory.build()

    await _store(media_uow_factory, asset)

    assert await _get(media_uow_factory, asset) == asset


async def test_media_repository_published_asset_round_trips_exif_and_location(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = _published(clock, ids)

    await _store(media_uow_factory, asset)
    loaded = await _get(media_uow_factory, asset)

    assert loaded == asset
    assert loaded is not None
    assert loaded.exif == EXIF


@pytest.mark.parametrize(
    "exif",
    [None, ExifFacts(), ExifFacts(camera="Test Camera 2")],
    ids=["no-exif", "empty-exif", "exif-without-location"],
)
async def test_media_repository_exif_presence_round_trips_exactly(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
    exif: ExifFacts | None,
) -> None:
    completed = _completed(MediaAssetTestFactory.build(created_at=CREATED), clock, ids)
    # Built directly: complete_upload folds empty EXIF into None, but the mapper
    # must keep the distinction for any asset the domain accepts.
    asset = MediaAsset.model_validate({**dict(completed), "exif": exif})

    await _store(media_uow_factory, asset)
    loaded = await _get(media_uow_factory, asset)

    assert loaded is not None
    assert loaded.exif == exif


async def test_media_repository_get_unknown_id_returns_none(
    media_uow_factory: MediaFactory,
) -> None:
    asset = MediaAssetTestFactory.build()

    assert await _get(media_uow_factory, asset) is None


async def test_media_repository_add_taken_id_raises_conflict_and_keeps_transaction(
    media_uow_factory: MediaFactory,
) -> None:
    asset = MediaAssetTestFactory.build()
    other = MediaAssetTestFactory.build()
    await _store(media_uow_factory, asset)

    async with media_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.media_assets.add(asset)
        await uow.media_assets.add(other)
        await uow.commit()

    assert await _get(media_uow_factory, other) == other


async def test_media_repository_save_next_version_persists_change(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = MediaAssetTestFactory.build(created_at=CREATED)
    await _store(media_uow_factory, asset)
    completed = _completed(asset, clock, ids)

    async with media_uow_factory() as uow:
        await uow.media_assets.save(completed)
        await uow.commit()

    assert await _get(media_uow_factory, asset) == completed


async def test_media_repository_save_stale_version_raises_conflict(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = MediaAssetTestFactory.build(created_at=CREATED)
    await _store(media_uow_factory, asset)
    completed = _completed(asset, clock, ids)
    async with media_uow_factory() as uow:
        await uow.media_assets.save(completed)
        await uow.commit()

    async with media_uow_factory() as uow:
        with pytest.raises(ConflictError) as raised:
            await uow.media_assets.save(completed)

    assert raised.value.details["stored_version"] == completed.version


async def test_media_repository_save_unknown_asset_raises_not_found(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    completed = _completed(MediaAssetTestFactory.build(created_at=CREATED), clock, ids)

    async with media_uow_factory() as uow:
        with pytest.raises(MediaAssetNotFoundError):
            await uow.media_assets.save(completed)


async def test_media_repository_find_completed_by_digest_returns_owners_oldest(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    owner = FACTORY_IDS.new_id()
    digest = synthetic_sha256()

    def build(offset: int, owner_id: EntityId = owner) -> MediaAsset:
        created = CREATED - timedelta(days=offset)
        return MediaAssetTestFactory.build(created_at=created, owner_id=owner_id)

    older = _completed(build(2), clock, ids, sha256=digest)
    newer = _completed(build(1), clock, ids, sha256=digest)
    # Older still, but the upload never completed, so it is not a duplicate.
    requested = build(3)
    other_owner = _completed(
        build(4, owner_id=FACTORY_IDS.new_id()), clock, ids, sha256=digest
    )
    other_digest = _completed(build(5), clock, ids)
    await _store(media_uow_factory, newer, older, requested, other_owner, other_digest)

    async with media_uow_factory() as uow:
        found = await uow.media_assets.find_completed_by_digest(owner, digest)
        missing = await uow.media_assets.find_completed_by_digest(
            owner, synthetic_sha256()
        )

    assert found == older
    assert missing is None


async def test_media_repository_find_by_digest_sees_uncommitted_write_in_same_uow(
    media_uow_factory: MediaFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = _completed(MediaAssetTestFactory.build(created_at=CREATED), clock, ids)
    assert asset.sha256 is not None

    async with media_uow_factory() as uow:
        await uow.media_assets.add(asset)
        found = await uow.media_assets.find_completed_by_digest(
            asset.owner_id, asset.sha256
        )

    assert found == asset
    assert await _get(media_uow_factory, asset) is None


async def test_media_assets_table_location_without_exif_violates_check(
    media_uow_factory: MediaFactory,
    session_factory: async_sessionmaker[AsyncSession],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    asset = _completed(MediaAssetTestFactory.build(created_at=CREATED), clock, ids)
    await _store(media_uow_factory, asset)

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="exif_location_needs_exif"):
            await session.execute(
                text("UPDATE media_assets SET exif = NULL WHERE id = :id"),
                {"id": asset.id},
            )
