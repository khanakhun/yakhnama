"""Unit tests for ``SweepStaleUploadsHandler``: abandoned uploads are failed."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from tests.fakes.media import InMemoryMediaAssetRepository
from tests.unit.modules.media.application.support import OWNER, Harness
from yakhnama.modules.media.application.commands import (
    RequestUpload,
    SweepStaleUploads,
)
from yakhnama.modules.media.application.dto import StoredObject
from yakhnama.modules.media.application.handlers import SweepStaleUploadsHandler
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.events import UploadFailed
from yakhnama.modules.media.domain.value_objects import (
    MimeType,
    UploadStatus,
    upload_object_key,
)
from yakhnama.shared_kernel.ids import EntityId

STALE_AFTER = timedelta(hours=1)


def sweeper(harness: Harness) -> SweepStaleUploadsHandler:
    """Return the sweeper over the harness's fakes."""
    return SweepStaleUploadsHandler(
        uow_factory=harness.factory,
        storage=harness.storage,
        stale_after=STALE_AFTER,
        clock=harness.clock,
        ids=harness.ids,
    )


async def requested(harness: Harness) -> EntityId:
    """Grant one upload and return its asset id."""
    grant = await harness.request()(
        RequestUpload(actor=OWNER, mime_type=MimeType.JPEG, byte_size=2048)
    )
    return grant.asset_id


async def test_sweep_fails_old_requested_uploads_and_deletes_their_objects() -> None:
    harness = Harness()
    asset_id = await requested(harness)
    key = upload_object_key(asset_id)
    harness.storage.objects[key] = StoredObject(
        sha256="a" * 64, byte_size=2048, content_type="image/jpeg"
    )
    harness.clock.advance(STALE_AFTER + timedelta(seconds=1))

    failed = await sweeper(harness)(SweepStaleUploads())

    asset = harness.uow.media_assets.committed[asset_id]
    assert failed == 1
    assert asset.upload_status is UploadStatus.FAILED
    assert harness.storage.deleted_uploads == [key]
    assert key not in harness.storage.objects
    assert isinstance(harness.uow.committed_events[-1], UploadFailed)


async def test_sweep_leaves_recent_and_completed_uploads_alone() -> None:
    harness = Harness()
    completed = await harness.uploaded()
    harness.clock.advance(STALE_AFTER)
    recent = await requested(harness)

    failed = await sweeper(harness)(SweepStaleUploads())

    assets = harness.uow.media_assets.committed
    assert failed == 0
    assert assets[completed.id].upload_status is UploadStatus.COMPLETED
    assert assets[recent].upload_status is UploadStatus.REQUESTED
    assert harness.storage.deleted_uploads == []


async def test_sweep_handles_at_most_one_batch_oldest_first() -> None:
    harness = Harness()
    first = await requested(harness)
    harness.clock.advance(timedelta(seconds=1))
    await requested(harness)
    harness.clock.advance(STALE_AFTER + timedelta(seconds=1))

    failed = await sweeper(harness)(SweepStaleUploads(batch_size=1))

    assert failed == 1
    assert harness.storage.deleted_uploads == [upload_object_key(first)]


class RacingMediaAssetRepository(InMemoryMediaAssetRepository):
    """Lists stale assets, then lets a concurrent completion end one of them.

    Implements: Fake (of Repository).
    """

    def __init__(self, harness: Harness) -> None:
        """Wrap the harness's committed assets."""
        super().__init__(harness.uow.media_assets.committed.values())
        self.harness = harness

    async def find_requested_before(
        self, before: datetime, limit: int
    ) -> Sequence[MediaAsset]:
        """List, then fail every listed asset as a concurrent request would."""
        listed = await super().find_requested_before(before, limit)
        for asset in listed:
            self.committed[asset.id] = asset.fail_upload(
                clock=self.harness.clock, ids=self.harness.ids
            ).state
        return listed


async def test_sweep_skips_an_asset_that_left_requested_meanwhile() -> None:
    harness = Harness()
    await requested(harness)
    harness.clock.advance(STALE_AFTER + timedelta(seconds=1))
    harness.uow.media_assets = RacingMediaAssetRepository(harness)

    failed = await sweeper(harness)(SweepStaleUploads())

    assert failed == 0
    assert harness.storage.deleted_uploads == []
