"""Unit tests for scan results and moderation, through to publication."""

import pytest

from tests.unit.modules.media.application.support import (
    MISSING_ID,
    MODERATOR,
    OWNER,
    Harness,
)
from yakhnama.modules.media.application.commands import (
    ModerateMedia,
    RecordScanResult,
)
from yakhnama.modules.media.domain.errors import (
    InvalidModerationDecisionError,
    MediaAssetNotFoundError,
)
from yakhnama.modules.media.domain.events import (
    MediaModerated,
    MediaPublished,
    MediaQuarantined,
    MediaScanned,
)
from yakhnama.modules.media.domain.value_objects import (
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
    original_object_key,
    public_object_key,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError


def event_types(harness: Harness) -> list[type]:
    """Return the committed event types, in order."""
    return [type(event) for event in harness.uow.committed_events]


# --------------------------------------------------------------------------- #
# RecordScanResult                                                            #
# --------------------------------------------------------------------------- #


async def test_record_scan_result_clean_commits_verdict() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    result = await harness.scan()(
        RecordScanResult(asset_id=asset.id, verdict=ScanStatus.CLEAN)
    )

    assert result.scan_status is ScanStatus.CLEAN
    assert harness.uow.media_assets.committed[asset.id].scan_status is ScanStatus.CLEAN
    assert event_types(harness)[-1] is MediaScanned


async def test_record_scan_result_infected_quarantines_asset() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    result = await harness.scan()(
        RecordScanResult(asset_id=asset.id, verdict=ScanStatus.INFECTED)
    )

    assert result.moderation_status is ModerationStatus.QUARANTINED
    assert event_types(harness)[-2:] == [MediaScanned, MediaQuarantined]


async def test_record_scan_result_repeated_verdict_records_one_event() -> None:
    harness = Harness()
    asset = await harness.uploaded()
    command = RecordScanResult(asset_id=asset.id, verdict=ScanStatus.UNAVAILABLE)

    await harness.scan()(command)
    await harness.scan()(command)

    assert event_types(harness).count(MediaScanned) == 1


async def test_record_scan_result_missing_asset_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(MediaAssetNotFoundError):
        await harness.scan()(
            RecordScanResult(asset_id=MISSING_ID, verdict=ScanStatus.CLEAN)
        )


# --------------------------------------------------------------------------- #
# ModerateMedia                                                               #
# --------------------------------------------------------------------------- #


async def test_moderate_media_clean_approved_asset_publishes_stripped_copy() -> None:
    harness = Harness()

    result = await harness.published()

    stored = harness.uow.media_assets.committed[result.id]
    assert result.is_published
    assert stored.public_key == public_object_key(result.id)
    assert harness.storage.copies == [
        (original_object_key(result.id), public_object_key(result.id))
    ]
    assert event_types(harness)[-2:] == [MediaModerated, MediaPublished]


async def test_moderate_media_repeated_approval_does_not_copy_again() -> None:
    harness = Harness()
    asset = await harness.published()

    await harness.moderate()(
        ModerateMedia(
            actor=MODERATOR, asset_id=asset.id, decision=ModerationStatus.APPROVED
        )
    )

    assert len(harness.storage.copies) == 1
    assert event_types(harness).count(MediaPublished) == 1


async def test_moderate_media_blocking_sensitivity_keeps_asset_private() -> None:
    harness = Harness()
    asset = await harness.uploaded()
    await harness.scan()(RecordScanResult(asset_id=asset.id, verdict=ScanStatus.CLEAN))

    result = await harness.moderate()(
        ModerateMedia(
            actor=MODERATOR,
            asset_id=asset.id,
            decision=ModerationStatus.APPROVED,
            sensitivity=SensitivityFlag.IDENTIFIABLE_PEOPLE,
        )
    )

    assert result.moderation_status is ModerationStatus.APPROVED
    assert not result.is_published
    assert harness.storage.copies == []


async def test_moderate_media_unscanned_approval_is_not_published() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    result = await harness.moderate()(
        ModerateMedia(
            actor=MODERATOR, asset_id=asset.id, decision=ModerationStatus.APPROVED
        )
    )

    assert not result.is_published
    assert harness.storage.copies == []


async def test_moderate_media_concurrently_published_asset_is_returned() -> None:
    harness = Harness()
    asset = await harness.uploaded()
    await harness.scan()(RecordScanResult(asset_id=asset.id, verdict=ScanStatus.CLEAN))
    repository = harness.uow.media_assets

    def publish_concurrently() -> None:
        current = repository.committed[asset.id]
        change = current.publish_public_copy(
            public_object_key(asset.id), clock=harness.clock, ids=harness.ids
        )
        repository.committed[asset.id] = change.state

    harness.storage.on_copy = publish_concurrently

    result = await harness.moderate()(
        ModerateMedia(
            actor=MODERATOR, asset_id=asset.id, decision=ModerationStatus.APPROVED
        )
    )

    assert result.is_published
    assert event_types(harness).count(MediaPublished) == 0


async def test_moderate_media_by_owner_raises_permission_denied() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    with pytest.raises(PermissionDeniedError):
        await harness.moderate()(
            ModerateMedia(
                actor=OWNER, asset_id=asset.id, decision=ModerationStatus.APPROVED
            )
        )

    assert (
        harness.uow.media_assets.committed[asset.id].moderation_status
        is ModerationStatus.PENDING
    )


async def test_moderate_media_rejection_without_reason_raises_domain_error() -> None:
    harness = Harness()
    asset = await harness.uploaded()

    with pytest.raises(InvalidModerationDecisionError):
        await harness.moderate()(
            ModerateMedia(
                actor=MODERATOR, asset_id=asset.id, decision=ModerationStatus.REJECTED
            )
        )


async def test_moderate_media_missing_asset_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(MediaAssetNotFoundError):
        await harness.moderate()(
            ModerateMedia(
                actor=MODERATOR, asset_id=MISSING_ID, decision=ModerationStatus.APPROVED
            )
        )
