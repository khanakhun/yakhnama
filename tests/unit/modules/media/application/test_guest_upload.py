"""Unit tests for guest uploads in the media module (ADR 0020)."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from tests.fakes.provenance import (
    FakePlatformSourceReferenceMarker,
    FakePlatformSourceRegistrar,
)
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
    MAX_MEDIA_BYTES,
    MimeType,
    UploadStatus,
    upload_object_key,
)
from yakhnama.modules.provenance.public import SourceType
from yakhnama.shared_kernel.errors import ConflictError, PermissionDeniedError
from yakhnama.shared_kernel.ids import EntityId

SUBMISSION_ID = IDS.new_id()
OTHER_SUBMISSION_ID = IDS.new_id()
GUEST_UPLOAD_TTL = timedelta(minutes=5)
PHOTO_BYTES = 1_234_567


class GuestFakes:
    """The guest upload handler over a media harness and platform source fakes."""

    def __init__(self, harness: Harness | None = None) -> None:
        """Arrange the fakes."""
        self.harness = Harness() if harness is None else harness
        self.registrar = FakePlatformSourceRegistrar()
        self.marker = FakePlatformSourceReferenceMarker(self.registrar)

    def handler(self) -> RequestGuestUploadHandler:
        """Return the guest upload-grant handler."""
        return RequestGuestUploadHandler(
            uow_factory=self.harness.factory,
            storage=self.harness.storage,
            source_registrar=self.registrar,
            source_marker=self.marker,
            upload_ttl=GUEST_UPLOAD_TTL,
            clock=self.harness.clock,
            ids=self.harness.ids,
        )


def guest_upload(
    asset_id: EntityId | None = None, mime_type: MimeType = MimeType.JPEG
) -> RequestGuestUpload:
    """Return a guest upload request for a fresh (or the given) asset id."""
    return RequestGuestUpload(
        owner_id=SUBMISSION_ID,
        asset_id=IDS.new_id() if asset_id is None else asset_id,
        mime_type=mime_type,
        byte_size=PHOTO_BYTES,
    )


async def test_request_guest_upload_creates_the_reserved_asset() -> None:
    fakes = GuestFakes()
    command = guest_upload(mime_type=MimeType.WEBP)

    grant = await fakes.handler()(command)

    asset = fakes.harness.uow.media_assets.committed[command.asset_id]
    [registration] = fakes.registrar.commands
    assert grant.asset_id == command.asset_id
    assert asset.owner_id == SUBMISSION_ID
    assert asset.report_id is None
    assert asset.source_id == fakes.registrar.registered[0].id
    assert registration.source_type is SourceType.CITIZEN
    assert registration.details.title == GUEST_UPLOAD_SOURCE_TITLE
    assert str(SUBMISSION_ID) not in registration.details.citation
    assert upload_object_key(grant.asset_id) in fakes.harness.storage.presigned_puts


async def test_request_guest_upload_signs_the_size_with_the_short_lifetime() -> None:
    fakes = GuestFakes()

    grant = await fakes.handler()(guest_upload())

    assert fakes.harness.storage.presigned_sizes == [(PHOTO_BYTES, GUEST_UPLOAD_TTL)]
    assert {(header.name, header.value) for header in grant.headers} >= {
        ("Content-Length", str(PHOTO_BYTES))
    }


async def test_request_guest_upload_marks_the_source_after_the_asset_is_stored() -> (
    None
):
    fakes = GuestFakes()

    grant = await fakes.handler()(guest_upload())

    asset = fakes.harness.uow.media_assets.committed[grant.asset_id]
    assert fakes.marker.marked_ids == (asset.source_id,)


async def test_request_guest_upload_for_a_taken_asset_id_conflicts_unmarked() -> None:
    fakes = GuestFakes()
    command = guest_upload()
    await fakes.handler()(command)

    with pytest.raises(ConflictError):
        await fakes.handler()(command)

    assert len(fakes.marker.commands) == 1


@pytest.mark.parametrize("mime_type", [MimeType.MP4, MimeType.PDF])
def test_request_guest_upload_non_image_is_refused(mime_type: MimeType) -> None:
    with pytest.raises(ValidationError, match="JPEG, PNG or WebP"):
        guest_upload(mime_type=mime_type)


@pytest.mark.parametrize("byte_size", [0, MAX_MEDIA_BYTES + 1])
def test_request_guest_upload_size_outside_the_cap_is_refused(byte_size: int) -> None:
    with pytest.raises(ValidationError):
        RequestGuestUpload(
            owner_id=SUBMISSION_ID,
            asset_id=IDS.new_id(),
            mime_type=MimeType.JPEG,
            byte_size=byte_size,
        )


async def test_complete_guest_upload_by_owning_submission_completes() -> None:
    fakes = GuestFakes()
    grant = await fakes.handler()(guest_upload())
    fakes.harness.upload(grant.asset_id)

    detail = await fakes.harness.complete()(
        CompleteGuestUpload(owner_id=SUBMISSION_ID, asset_id=grant.asset_id)
    )

    assert detail.upload_status is UploadStatus.COMPLETED


async def test_complete_guest_upload_by_other_submission_is_denied() -> None:
    fakes = GuestFakes()
    grant = await fakes.handler()(guest_upload())
    fakes.harness.upload(grant.asset_id)

    with pytest.raises(PermissionDeniedError):
        await fakes.harness.complete()(
            CompleteGuestUpload(owner_id=OTHER_SUBMISSION_ID, asset_id=grant.asset_id)
        )

    stored = fakes.harness.uow.media_assets.committed[grant.asset_id]
    assert stored.upload_status is UploadStatus.REQUESTED


async def test_complete_guest_upload_of_user_asset_is_denied() -> None:
    harness = Harness()
    completed = await harness.uploaded()

    with pytest.raises(PermissionDeniedError):
        await harness.complete()(
            CompleteGuestUpload(owner_id=SUBMISSION_ID, asset_id=completed.id)
        )

    assert harness.uow.media_assets.committed[completed.id].owner_id == OWNER_ID
