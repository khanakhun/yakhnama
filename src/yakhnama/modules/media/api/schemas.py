"""Request and response bodies of the media HTTP API.

``UploadGrant`` (the presigned upload URL, its headers, expiry and size cap) is
returned as it is. Assets are returned as ``MediaAssetResponse``, a field-for-field
copy of the ``MediaAssetDetail`` DTO (statuses and the presigned download links the
caller may use; never object keys, EXIF or the owner). The copy exists for one
reason: the media ``ModerationStatus`` enum shares its class name with the identity
API's ``ModerationStatus`` body, and letting the enum class into OpenAPI would
rename the committed identity component. The API therefore spells the moderation
statuses as string literals (``ModerationStatusName``, ``ModerationDecision``); a
test keeps them equal to the enum until the names are told apart at the source
(open question).

Patterns: API Schema.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.media.domain.value_objects import (
    ByteSize,
    MediaVersion,
    ModerationReason,
)
from yakhnama.modules.media.public import (
    MediaAssetDetail,
    MimeType,
    ModerationStatus,
    PresignedDownload,
    ScanStatus,
    SensitivityFlag,
    UploadStatus,
)
from yakhnama.shared_kernel.ids import EntityId

ModerationStatusName = Literal["pending", "approved", "rejected", "quarantined"]
"""The values of the media ``ModerationStatus`` enum, spelled out for OpenAPI."""

ModerationDecision = Literal["approved", "rejected"]
"""The statuses a moderator may decide; ``ModerateMedia`` refuses any other."""

MODERATION_STATUS_NAMES: Final[tuple[ModerationStatusName, ...]] = (
    "pending",
    "approved",
    "rejected",
    "quarantined",
)


_STATUS_NAMES: Final[dict[ModerationStatus, ModerationStatusName]] = {
    ModerationStatus(name): name for name in MODERATION_STATUS_NAMES
}


class RequestUploadRequest(BaseModel):
    """Body of ``POST /api/v1/media`` and ``POST /api/v1/reports/{id}/media``.

    Implements: API Schema.

    Attributes:
        mime_type: The declared media type, from the allow-list; the file's
            content is checked against it again when the upload completes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mime_type: MimeType


class ModerateMediaRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/media/{asset_id}/decision``.

    Implements: API Schema.

    Attributes:
        decision: ``approved`` or ``rejected``.
        sensitivity: Why the asset needs care, ``none`` by default.
        reason: Why, 1 to 500 characters of safe text; required for a rejection.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ModerationDecision
    sensitivity: SensitivityFlag = SensitivityFlag.NONE
    reason: ModerationReason | None = None


class MediaAssetResponse(BaseModel):
    """One media asset as a client sees it.

    Implements: API Schema.

    Attributes:
        id: The asset's id.
        report_id: The report it belongs to, if any.
        mime_type: The media type.
        byte_size: Size of the original, once completed.
        upload_status: Whether the file arrived.
        scan_status: The scanner's verdict.
        moderation_status: The moderator's decision.
        sensitivity: The sensitivity flag.
        is_published: Whether a public copy exists.
        public_download: Link to the public copy, if the caller gets one.
        original_download: Link to the private original, for its owner and
            moderators only.
        version: Optimistic-concurrency version, for ``ETag``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    report_id: EntityId | None
    mime_type: MimeType
    byte_size: ByteSize | None
    upload_status: UploadStatus
    scan_status: ScanStatus
    moderation_status: ModerationStatusName
    sensitivity: SensitivityFlag
    is_published: bool
    public_download: PresignedDownload | None
    original_download: PresignedDownload | None
    version: MediaVersion

    @classmethod
    def from_detail(cls, detail: MediaAssetDetail) -> Self:
        """Copy the DTO into the transport representation.

        Args:
            detail: The application's view of the asset.

        Returns:
            The same values.
        """
        return cls(
            id=detail.id,
            report_id=detail.report_id,
            mime_type=detail.mime_type,
            byte_size=detail.byte_size,
            upload_status=detail.upload_status,
            scan_status=detail.scan_status,
            moderation_status=_STATUS_NAMES[detail.moderation_status],
            sensitivity=detail.sensitivity,
            is_published=detail.is_published,
            public_download=detail.public_download,
            original_download=detail.original_download,
            version=detail.version,
        )
