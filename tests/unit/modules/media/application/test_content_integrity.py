"""Unit tests for the security rules: changed originals and unpublishable types."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.media import MediaAssetTestFactory
from tests.unit.modules.media.application.support import MODERATOR, OWNER, Harness
from yakhnama.modules.media.application.commands import (
    CompleteUpload,
    ModerateMedia,
    RecordScanResult,
    RequestUpload,
)
from yakhnama.modules.media.application.handlers import (
    CONTENT_CHANGED_QUARANTINE_REASON,
    _recorded_digest,  # reason: tests the fail-loud guard behind an invariant
)
from yakhnama.modules.media.domain.errors import MediaContentChangedError
from yakhnama.modules.media.domain.events import MediaQuarantined, MediaScanned
from yakhnama.modules.media.domain.value_objects import (
    MimeType,
    ModerationStatus,
    ScanStatus,
    original_object_key,
)
from yakhnama.shared_kernel.errors import InvariantViolationError


async def test_record_scan_result_changed_content_quarantines_the_asset() -> None:
    harness = Harness()
    asset = await harness.uploaded()
    await harness.scan()(RecordScanResult(asset_id=asset.id, verdict=ScanStatus.CLEAN))

    result = await harness.scan()(
        RecordScanResult(
            asset_id=asset.id, verdict=ScanStatus.UNAVAILABLE, is_content_changed=True
        )
    )

    stored = harness.uow.media_assets.committed[asset.id]
    assert result.moderation_status is ModerationStatus.QUARANTINED
    assert stored.scan_status is ScanStatus.UNAVAILABLE
    assert stored.moderation_reason == CONTENT_CHANGED_QUARANTINE_REASON
    assert [type(event) for event in harness.uow.committed_events][-2:] == [
        MediaScanned,
        MediaQuarantined,
    ]


async def test_record_scan_result_changed_content_repeated_commits_nothing() -> None:
    harness = Harness()
    asset = await harness.uploaded()
    command = RecordScanResult(
        asset_id=asset.id, verdict=ScanStatus.UNAVAILABLE, is_content_changed=True
    )
    await harness.scan()(command)
    events_before = len(harness.uow.committed_events)

    await harness.scan()(command)

    assert len(harness.uow.committed_events) == events_before


@pytest.mark.parametrize("verdict", [ScanStatus.CLEAN, ScanStatus.INFECTED])
def test_record_scan_result_changed_content_with_a_verdict_is_invalid(
    verdict: ScanStatus,
) -> None:
    with pytest.raises(PydanticValidationError, match="only have the verdict"):
        RecordScanResult.model_validate(
            {
                "asset_id": "0197a000-0000-7000-8000-000000000001",
                "verdict": verdict,
                "is_content_changed": True,
            }
        )


async def test_moderate_media_changed_original_is_quarantined_not_published() -> None:
    harness = Harness()
    asset = await harness.uploaded()
    await harness.scan()(RecordScanResult(asset_id=asset.id, verdict=ScanStatus.CLEAN))
    key = original_object_key(asset.id)
    harness.storage.objects[key] = harness.storage.objects[key].model_copy(
        update={"sha256": "f" * 64}
    )

    with pytest.raises(MediaContentChangedError) as raised:
        await harness.moderate()(
            ModerateMedia(
                actor=MODERATOR, asset_id=asset.id, decision=ModerationStatus.APPROVED
            )
        )

    stored = harness.uow.media_assets.committed[asset.id]
    assert raised.value.details == {
        "media_id": str(asset.id),
        "reason": "media_content_changed",
    }
    assert stored.moderation_status is ModerationStatus.QUARANTINED
    assert stored.scan_status is ScanStatus.UNAVAILABLE
    assert not stored.is_published
    assert harness.storage.copies == []


@pytest.mark.parametrize("mime_type", [MimeType.MP4, MimeType.PDF])
async def test_moderate_media_approved_video_or_pdf_is_never_published(
    mime_type: MimeType,
) -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=mime_type))
    harness.upload(grant.asset_id)
    harness.sniffer.types[original_object_key(grant.asset_id)] = mime_type
    await harness.complete()(CompleteUpload(actor=OWNER, asset_id=grant.asset_id))
    await harness.scan()(
        RecordScanResult(asset_id=grant.asset_id, verdict=ScanStatus.CLEAN)
    )

    result = await harness.moderate()(
        ModerateMedia(
            actor=MODERATOR,
            asset_id=grant.asset_id,
            decision=ModerationStatus.APPROVED,
        )
    )

    assert result.moderation_status is ModerationStatus.APPROVED
    assert not result.is_published
    assert harness.storage.copies == []


def test_recorded_digest_of_an_asset_without_one_fails_loudly() -> None:
    requested = MediaAssetTestFactory.build()

    with pytest.raises(InvariantViolationError):
        _recorded_digest(requested)
