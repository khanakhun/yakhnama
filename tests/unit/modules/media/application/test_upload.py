"""Unit tests for requesting and completing uploads, deduplication included."""

import pytest

from tests.unit.modules.media.application.support import (
    EXIF,
    MISSING_ID,
    OTHER_CITIZEN,
    OTHER_ID,
    OWNER,
    OWNER_ID,
    REPORT_ID,
    REPORT_SOURCE_ID,
    Harness,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.application.commands import CompleteUpload, RequestUpload
from yakhnama.modules.media.application.dto import StoredObject
from yakhnama.modules.media.application.handlers import UPLOAD_SOURCE_TITLE
from yakhnama.modules.media.application.ports import SCAN_TASK
from yakhnama.modules.media.domain.errors import (
    MediaAssetNotFoundError,
    MediaUploadNotPendingError,
)
from yakhnama.modules.media.domain.events import (
    UploadCompleted,
    UploadFailed,
    UploadRequested,
)
from yakhnama.modules.media.domain.value_objects import (
    MAX_MEDIA_BYTES,
    MimeType,
    UploadStatus,
    original_object_key,
)
from yakhnama.modules.provenance.public import SourceType
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError

# --------------------------------------------------------------------------- #
# RequestUpload                                                               #
# --------------------------------------------------------------------------- #


async def test_request_upload_without_report_registers_and_cites_source() -> None:
    harness = Harness()

    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.PNG))

    asset = harness.uow.media_assets.committed[grant.asset_id]
    [registration] = harness.registrar.commands
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == UPLOAD_SOURCE_TITLE
    assert asset.source_id == harness.registrar.registered[0].id
    assert harness.marker.marked_ids == (asset.source_id,)
    assert asset.owner_id == OWNER_ID
    assert asset.upload_status is UploadStatus.REQUESTED
    assert [type(event) for event in harness.uow.committed_events] == [UploadRequested]


async def test_request_upload_grants_presigned_put_for_private_original() -> None:
    harness = Harness()

    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.PNG))

    assert harness.storage.presigned_puts == [original_object_key(grant.asset_id)]
    assert grant.max_bytes == MAX_MEDIA_BYTES
    assert ("Content-Type", "image/png") in [(h.name, h.value) for h in grant.headers]
    assert "private" in grant.upload_url


async def test_request_upload_for_own_report_uses_report_source() -> None:
    harness = Harness()

    grant = await harness.request()(
        RequestUpload(actor=OWNER, report_id=REPORT_ID, mime_type=MimeType.JPEG)
    )

    asset = harness.uow.media_assets.committed[grant.asset_id]
    assert asset.source_id == REPORT_SOURCE_ID
    assert asset.report_id == REPORT_ID
    assert harness.registrar.commands == []
    assert harness.marker.commands == []


@pytest.mark.parametrize("report_id", [REPORT_ID, MISSING_ID])
async def test_request_upload_for_foreign_or_missing_report_raises_denied(
    report_id: object,
) -> None:
    harness = Harness()

    with pytest.raises(PermissionDeniedError):
        await harness.request()(
            RequestUpload.model_validate(
                {
                    "actor": OTHER_CITIZEN,
                    "report_id": report_id,
                    "mime_type": MimeType.JPEG,
                }
            )
        )

    assert harness.uow.media_assets.committed == {}
    assert harness.storage.presigned_puts == []


async def test_request_upload_anonymous_raises_permission_denied() -> None:
    harness = Harness()

    with pytest.raises(PermissionDeniedError):
        await harness.request()(
            RequestUpload(actor=Actor.anonymous(), mime_type=MimeType.JPEG)
        )

    assert harness.registrar.commands == []


# --------------------------------------------------------------------------- #
# CompleteUpload                                                              #
# --------------------------------------------------------------------------- #


async def test_complete_upload_stored_file_completes_and_enqueues_scan() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.PNG))
    digest = harness.upload(grant.asset_id)

    result = await harness.complete()(
        CompleteUpload(actor=OWNER, asset_id=grant.asset_id)
    )

    asset = harness.uow.media_assets.committed[grant.asset_id]
    assert asset.upload_status is UploadStatus.COMPLETED
    assert asset.sha256 == digest
    assert asset.mime_type is MimeType.JPEG  # detected, not declared
    assert asset.exif == EXIF
    [task] = harness.tasks.of(SCAN_TASK)
    assert dict(task.payload) == {"asset_id": grant.asset_id}
    assert UploadCompleted in [type(event) for event in harness.uow.committed_events]
    assert result.upload_status is UploadStatus.COMPLETED
    assert "exif" not in type(result).model_fields


async def test_complete_upload_repeated_returns_asset_without_second_scan() -> None:
    harness = Harness()
    first = await harness.uploaded()

    second = await harness.complete()(CompleteUpload(actor=OWNER, asset_id=first.id))

    assert second == first
    assert len(harness.tasks.enqueued) == 1


async def test_complete_upload_before_file_arrived_raises_and_changes_nothing() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))
    events_before = len(harness.uow.committed_events)

    with pytest.raises(ValidationError) as raised:
        await harness.complete()(CompleteUpload(actor=OWNER, asset_id=grant.asset_id))

    asset = harness.uow.media_assets.committed[grant.asset_id]
    assert asset.upload_status is UploadStatus.REQUESTED
    assert raised.value.details["reason"] == "object_missing"
    assert len(harness.uow.committed_events) == events_before


async def test_complete_upload_disallowed_type_marks_failed_and_raises() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))
    harness.upload(grant.asset_id)
    del harness.sniffer.types[original_object_key(grant.asset_id)]

    with pytest.raises(ValidationError) as raised:
        await harness.complete()(CompleteUpload(actor=OWNER, asset_id=grant.asset_id))

    asset = harness.uow.media_assets.committed[grant.asset_id]
    assert asset.upload_status is UploadStatus.FAILED
    assert raised.value.details["reason"] == "mime_type"
    assert type(harness.uow.committed_events[-1]) is UploadFailed
    assert harness.tasks.enqueued == []


async def test_complete_upload_empty_file_marks_failed_and_raises() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))
    harness.upload(grant.asset_id)
    key = original_object_key(grant.asset_id)
    harness.storage.objects[key] = harness.storage.objects[key].model_copy(
        update={"byte_size": 0}
    )

    with pytest.raises(ValidationError) as raised:
        await harness.complete()(CompleteUpload(actor=OWNER, asset_id=grant.asset_id))

    assert raised.value.details["reason"] == "size"
    assert (
        harness.uow.media_assets.committed[grant.asset_id].upload_status
        is UploadStatus.FAILED
    )


async def test_complete_upload_after_failure_raises_not_pending() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))
    harness.storage.objects[original_object_key(grant.asset_id)] = StoredObject(
        sha256="a" * 64, byte_size=0
    )
    with pytest.raises(ValidationError):
        await harness.complete()(CompleteUpload(actor=OWNER, asset_id=grant.asset_id))

    with pytest.raises(MediaUploadNotPendingError):
        await harness.complete()(CompleteUpload(actor=OWNER, asset_id=grant.asset_id))


async def test_complete_upload_by_other_user_raises_permission_denied() -> None:
    harness = Harness()
    grant = await harness.request()(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))
    harness.upload(grant.asset_id)

    with pytest.raises(PermissionDeniedError):
        await harness.complete()(
            CompleteUpload(actor=OTHER_CITIZEN, asset_id=grant.asset_id)
        )

    assert (
        harness.uow.media_assets.committed[grant.asset_id].upload_status
        is UploadStatus.REQUESTED
    )


async def test_complete_upload_missing_asset_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(MediaAssetNotFoundError):
        await harness.complete()(CompleteUpload(actor=OWNER, asset_id=MISSING_ID))


async def test_complete_upload_same_file_twice_returns_first_and_fails_second() -> None:
    harness = Harness()
    first = await harness.uploaded()
    digest = harness.uow.media_assets.committed[first.id].sha256

    second = await harness.uploaded(sha256=digest)

    duplicates = [
        asset
        for asset in harness.uow.media_assets.committed.values()
        if asset.id != first.id
    ]
    assert second.id == first.id
    assert [asset.upload_status for asset in duplicates] == [UploadStatus.FAILED]
    assert len(harness.tasks.of(SCAN_TASK)) == 1


async def test_complete_upload_same_file_from_other_owner_is_not_duplicate() -> None:
    harness = Harness()
    first = await harness.uploaded()
    digest = harness.uow.media_assets.committed[first.id].sha256

    second = await harness.uploaded(actor_id=OTHER_ID, sha256=digest)

    assert second.id != first.id
    assert second.upload_status is UploadStatus.COMPLETED
    assert len(harness.tasks.of(SCAN_TASK)) == 2
