"""Domain events of the ``media`` bounded context.

Every event carries the asset's id as ``aggregate_id``, ``"media_asset"`` as
``aggregate_type`` and the asset's ``version`` after the change. **Payloads carry ids,
statuses, the media type and the size only**: never an object key (it would let a
subscriber fetch a private original), never EXIF facts (the GPS position can locate a
person) and never a moderation reason (free text), because events are relayed to
subscribers and kept in the outbox (``AGENTS.md`` §5).

Patterns: Domain Events.
"""

from typing import ClassVar, Literal

from yakhnama.modules.media.domain.value_objects import (
    MEDIA_AGGREGATE_TYPE,
    ByteSize,
    MediaVersion,
    MimeType,
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId


class MediaEvent(DomainEvent):
    """Fields shared by every event about a media asset; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"media_asset"``.
        version: The asset's version after the change.
    """

    aggregate_type: Literal["media_asset"] = MEDIA_AGGREGATE_TYPE
    version: MediaVersion


class UploadRequested(MediaEvent):
    """An upload slot was created for a new asset.

    Implements: Domain Events.

    Attributes:
        owner_id: The uploading user.
        report_id: The report the asset belongs to, or ``None``.
        source_id: The provenance source of the asset.
        mime_type: The media type the uploader declared.
    """

    event_type: ClassVar[str] = "media.upload_requested"

    owner_id: EntityId
    report_id: EntityId | None
    source_id: EntityId
    mime_type: MimeType


class UploadCompleted(MediaEvent):
    """The file arrived in private storage and its facts were recorded.

    Implements: Domain Events.

    Attributes:
        mime_type: The media type detected from the file's content.
        byte_size: The file size in bytes.
        has_exif: Whether any EXIF fact was found (the facts stay on the asset).
    """

    event_type: ClassVar[str] = "media.upload_completed"

    mime_type: MimeType
    byte_size: ByteSize
    has_exif: bool


class UploadFailed(MediaEvent):
    """The upload did not arrive or did not match what was declared.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "media.upload_failed"


class MediaScanned(MediaEvent):
    """The malware scanner returned a verdict on the original.

    Implements: Domain Events.

    Attributes:
        scan_status: The verdict.
    """

    event_type: ClassVar[str] = "media.media_scanned"

    scan_status: ScanStatus


class MediaModerated(MediaEvent):
    """A moderator approved or rejected the asset, or changed its sensitivity.

    Implements: Domain Events.

    Attributes:
        moderation_status: The decision.
        sensitivity: The sensitivity flag after the decision.
        is_public_copy_withdrawn: Whether a published public copy must now be
            deleted from public storage.
    """

    event_type: ClassVar[str] = "media.media_moderated"

    moderation_status: ModerationStatus
    sensitivity: SensitivityFlag
    is_public_copy_withdrawn: bool


class MediaPublished(MediaEvent):
    """An EXIF-stripped public copy of the asset was published.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "media.media_published"


class MediaQuarantined(MediaEvent):
    """The asset was isolated, by a moderator or after an infected scan.

    Implements: Domain Events.

    Attributes:
        is_public_copy_withdrawn: Whether a published public copy must now be
            deleted from public storage.
    """

    event_type: ClassVar[str] = "media.media_quarantined"

    is_public_copy_withdrawn: bool
