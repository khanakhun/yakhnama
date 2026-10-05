"""Unit tests for guest uploads in the media module (ADR 0020)."""

import pytest
from pydantic import ValidationError

from tests.fakes.provenance import FakePlatformSourceRegistrar
from tests.unit.modules.media.application.support import (
    IDS,
    OWNER_ID,
    Harness,
)
from yakhnama.modules.media.application.commands import (
    CompleteGuestUpload,
    RequestGuestUpload,
)
from yakhnama.modules.media.application.handlers import (
    GUEST_UPLOAD_SOURCE_TITLE,
    RequestGuestUploadHandler,
)
from yakhnama.modules.media.domain.value_objects import (
    MimeType,
    UploadStatus,
    upload_object_key,
)
from yakhnama.modules.provenance.public import SourceType
from yakhnama.shared_kernel.errors import PermissionDeniedError

SUBMISSION_ID = IDS.new_id()
OTHER_SUBMISSION_ID = IDS.new_id()


def guest_request(
    harness: Harness, registrar: FakePlatformSourceRegistrar
) -> RequestGuestUploadHandler:
    """Return the guest upload-grant handler over the harness's fakes."""
    return RequestGuestUploadHandler(
        uow_factory=harness.factory,
        storage=harness.storage,
        source_registrar=registrar,
        clock=harness.clock,
        ids=harness.ids,
    )


async def test_request_guest_upload_creates_asset_owned_by_submission() -> None:
    harness = Harness()
    registrar = FakePlatformSourceRegistrar()

    grant = await guest_request(harness, registrar)(
        RequestGuestUpload(owner_id=SUBMISSION_ID, mime_type=MimeType.WEBP)
    )

    asset = harness.uow.media_assets.committed[grant.asset_id]
    [registration] = registrar.commands
    assert asset.owner_id == SUBMISSION_ID
    assert asset.report_id is None
    assert asset.source_id == registrar.registered[0].id
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == GUEST_UPLOAD_SOURCE_TITLE
    assert str(SUBMISSION_ID) not in registration.details.citation
    assert upload_object_key(grant.asset_id) in harness.storage.presigned_puts


@pytest.mark.parametrize("mime_type", [MimeType.MP4, MimeType.PDF])
def test_request_guest_upload_non_image_is_refused(mime_type: MimeType) -> None:
    with pytest.raises(ValidationError, match="JPEG, PNG or WebP"):
        RequestGuestUpload(owner_id=SUBMISSION_ID, mime_type=mime_type)


async def test_complete_guest_upload_by_owning_submission_completes() -> None:
    harness = Harness()
    grant = await guest_request(harness, FakePlatformSourceRegistrar())(
        RequestGuestUpload(owner_id=SUBMISSION_ID, mime_type=MimeType.JPEG)
    )
    harness.upload(grant.asset_id)

    detail = await harness.complete()(
        CompleteGuestUpload(owner_id=SUBMISSION_ID, asset_id=grant.asset_id)
    )

    assert detail.upload_status is UploadStatus.COMPLETED


async def test_complete_guest_upload_by_other_submission_is_denied() -> None:
    harness = Harness()
    grant = await guest_request(harness, FakePlatformSourceRegistrar())(
        RequestGuestUpload(owner_id=SUBMISSION_ID, mime_type=MimeType.JPEG)
    )
    harness.upload(grant.asset_id)

    with pytest.raises(PermissionDeniedError):
        await harness.complete()(
            CompleteGuestUpload(owner_id=OTHER_SUBMISSION_ID, asset_id=grant.asset_id)
        )

    stored = harness.uow.media_assets.committed[grant.asset_id]
    assert stored.upload_status is UploadStatus.REQUESTED


async def test_complete_guest_upload_of_user_asset_is_denied() -> None:
    harness = Harness()
    completed = await harness.uploaded()

    with pytest.raises(PermissionDeniedError):
        await harness.complete()(
            CompleteGuestUpload(owner_id=SUBMISSION_ID, asset_id=completed.id)
        )

    assert harness.uow.media_assets.committed[completed.id].owner_id == OWNER_ID
