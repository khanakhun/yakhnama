"""Transfer objects of the media module: storage answers, grants and asset views.

``MediaAssetRecord`` is the **internal** read model: it holds the storage keys and
the private EXIF facts of the original (including a GPS position) and never reaches
an API client. Clients receive ``UploadGrant`` and ``MediaAssetDetail``, which carry
no keys and no EXIF; downloads are presigned links that expire.

Patterns: DTO.
"""

from typing import Annotated, Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.value_objects import (
    ByteSize,
    ExifFacts,
    MediaVersion,
    MimeType,
    ModerationStatus,
    ObjectKey,
    ScanStatus,
    SensitivityFlag,
    Sha256,
    UploadStatus,
)
from yakhnama.shared_kernel.ids import EntityId

PRESIGNED_URL_MAX_LENGTH: Final = 4096
HEADERS_MAX: Final = 16
HEADER_NAME_PATTERN: Final = r"^[A-Za-z0-9-]{1,64}$"

PresignedUrl = Annotated[
    str, StringConstraints(min_length=1, max_length=PRESIGNED_URL_MAX_LENGTH)
]
"""A presigned storage URL; it grants access until it expires, so it is not logged."""

HeaderName = Annotated[str, StringConstraints(pattern=HEADER_NAME_PATTERN)]
HeaderValue = Annotated[str, StringConstraints(pattern=r"^[\x20-\x7e]{0,1024}$")]


class HttpHeader(BaseModel):
    """One header the client must send with a presigned request.

    Implements: DTO.

    Attributes:
        name: Header name, for example ``Content-Type``.
        value: Header value, printable ASCII.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: HeaderName
    value: HeaderValue


class PresignedUpload(BaseModel):
    """What the storage adapter returns for a presigned ``PUT``.

    Implements: DTO.

    Attributes:
        url: Where to upload.
        headers: Headers the upload must carry.
        expires_at: When the URL stops working, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: PresignedUrl
    headers: tuple[HttpHeader, ...] = Field(default=(), max_length=HEADERS_MAX)
    expires_at: AwareDatetime


class PresignedDownload(BaseModel):
    """A presigned ``GET`` link to one stored object.

    Implements: DTO.

    Attributes:
        url: Where to download.
        expires_at: When the URL stops working, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: PresignedUrl
    expires_at: AwareDatetime


class StoredObject(BaseModel):
    """What storage says about an object that exists.

    Implements: DTO.

    Attributes:
        sha256: Digest of the stored bytes, computed by the adapter.
        byte_size: Size of the stored bytes; 0 for an empty upload.
        content_type: The ``Content-Type`` stored with the object, if any; the
            client chose it, so it is never trusted over ``MimeSniffer``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: Sha256
    byte_size: int = Field(ge=0)
    content_type: Annotated[str, StringConstraints(max_length=255)] | None = None


class UploadGrant(BaseModel):
    """What the uploader receives: which asset, where to upload and until when.

    Implements: DTO.

    Attributes:
        asset_id: The new asset; the client sends it to ``CompleteUpload``.
        upload_url: The presigned ``PUT`` URL.
        headers: Headers the upload must carry.
        expires_at: When the URL stops working, UTC.
        max_bytes: Largest accepted file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: EntityId
    upload_url: PresignedUrl
    headers: tuple[HttpHeader, ...] = Field(max_length=HEADERS_MAX)
    expires_at: AwareDatetime
    max_bytes: ByteSize


class MediaAssetRecord(BaseModel):
    """One asset as the read side stores it, keys and EXIF included (internal).

    Implements: DTO.

    Attributes:
        id: The asset's id.
        owner_id: The uploader.
        report_id: The report it belongs to, if any.
        source_id: The provenance source.
        original_key: Storage key of the private original.
        public_key: Storage key of the public copy, once published.
        mime_type: The detected (or, before completion, declared) media type.
        byte_size: Size of the original, once completed.
        exif: EXIF facts of the original, if any; private.
        upload_status: Whether the file arrived.
        scan_status: The scanner's verdict.
        moderation_status: The moderator's decision.
        sensitivity: The sensitivity flag.
        version: Optimistic-concurrency version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    owner_id: EntityId
    report_id: EntityId | None
    source_id: EntityId
    original_key: ObjectKey
    public_key: ObjectKey | None
    mime_type: MimeType
    byte_size: ByteSize | None
    exif: ExifFacts | None
    upload_status: UploadStatus
    scan_status: ScanStatus
    moderation_status: ModerationStatus
    sensitivity: SensitivityFlag
    version: MediaVersion

    @classmethod
    def from_entity(cls, asset: MediaAsset) -> Self:
        """Build the record of an asset.

        Args:
            asset: The aggregate.

        Returns:
            Its record.
        """
        return cls(
            id=asset.id,
            owner_id=asset.owner_id,
            report_id=asset.report_id,
            source_id=asset.source_id,
            original_key=asset.original_key,
            public_key=asset.public_key,
            mime_type=asset.mime_type,
            byte_size=asset.byte_size,
            exif=asset.exif,
            upload_status=asset.upload_status,
            scan_status=asset.scan_status,
            moderation_status=asset.moderation_status,
            sensitivity=asset.sensitivity,
            version=asset.version,
        )

    @property
    def is_published(self) -> bool:
        """Tell whether a public copy exists.

        Returns:
            ``True`` if ``public_key`` is set.
        """
        return self.public_key is not None


class MediaAssetDetail(BaseModel):
    """One asset as a client sees it: statuses and presigned links, no keys or EXIF.

    Implements: DTO.

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
        public_download: Link to the public copy, if published and asked for.
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
    moderation_status: ModerationStatus
    sensitivity: SensitivityFlag
    is_published: bool
    public_download: PresignedDownload | None = None
    original_download: PresignedDownload | None = None
    version: MediaVersion

    @classmethod
    def from_record(
        cls,
        record: MediaAssetRecord,
        *,
        public_download: PresignedDownload | None = None,
        original_download: PresignedDownload | None = None,
    ) -> Self:
        """Build the client view of an asset.

        Args:
            record: The internal record.
            public_download: Link to the public copy, if the caller gets one.
            original_download: Link to the original, if the caller gets one.

        Returns:
            The view.
        """
        return cls(
            id=record.id,
            report_id=record.report_id,
            mime_type=record.mime_type,
            byte_size=record.byte_size,
            upload_status=record.upload_status,
            scan_status=record.scan_status,
            moderation_status=record.moderation_status,
            sensitivity=record.sensitivity,
            is_published=record.is_published,
            public_download=public_download,
            original_download=original_download,
            version=record.version,
        )

    @classmethod
    def from_entity(cls, asset: MediaAsset) -> Self:
        """Build the view of an asset without download links.

        Args:
            asset: The aggregate.

        Returns:
            The view.
        """
        return cls.from_record(MediaAssetRecord.from_entity(asset))
