"""Value objects of the ``media`` bounded context.

A media asset is one uploaded file (a photo, a video or a document) attached to a
report. Its original is kept private; a separate public copy, with EXIF metadata
stripped, is published only after a clean malware scan and a moderator's approval.

Patterns: Value Object.
"""

from enum import StrEnum
from typing import Annotated, Final
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)

from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import Coordinates, DateWithPrecision

MEDIA_AGGREGATE_TYPE: Final = "media_asset"

OBJECT_KEY_PATTERN: Final = r"^[a-z0-9][a-z0-9/_.-]{3,255}$"


def _reject_parent_segments(key: str) -> str:
    # A ".." anywhere could climb out of the intended prefix in a storage backend or
    # a filesystem-backed test double, so it is refused even where S3 would not care.
    if ".." in key:
        message = "an object key must not contain '..'"
        raise ValueError(message)
    return key


ObjectKey = Annotated[
    str,
    StringConstraints(pattern=OBJECT_KEY_PATTERN),
    AfterValidator(_reject_parent_segments),
]
"""A storage object key: lower-case ASCII letters, digits and ``/_.-``, 4-256 long.

Keys are built by the platform (``upload_object_key``, ``original_object_key``,
``public_object_key``), never from a file name the uploader chose, so they carry no
personal data.
"""

SHA256_PATTERN: Final = r"^[a-f0-9]{64}$"


def _lower_hex(value: object) -> object:
    # Hex digits are case-insensitive; one canonical case makes equal digests equal
    # strings, which the deduplication rule compares.
    return value.lower() if isinstance(value, str) else value


Sha256 = Annotated[
    str, BeforeValidator(_lower_hex), StringConstraints(pattern=SHA256_PATTERN)
]
"""A SHA-256 digest as 64 lower-case hexadecimal digits (upper case is folded)."""


class MimeType(StrEnum):
    """The media types an upload may have (**proposed** allow-list, Q-M1).

    The type is checked against the file's magic bytes by an adapter at completion;
    the declared type alone is never trusted.

    Implements: Value Object.
    """

    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"
    MP4 = "video/mp4"
    PDF = "application/pdf"


PUBLISHABLE_MIME_TYPES: Final = frozenset({MimeType.JPEG, MimeType.PNG, MimeType.WEBP})
"""Types whose public copy can be stripped of embedded metadata.

Images are re-encoded from pixels alone. No stripper exists yet for MP4 (``udta``
boxes may hold a GPS position) or PDF (document information may name the author),
so those stay private however they are moderated (Q-M13).
"""

MAX_MEDIA_BYTES: Final = 50 * 1024 * 1024
"""Largest accepted upload: 50 MiB (**proposed**, Q-M2)."""

ByteSize = Annotated[int, Field(ge=1, le=MAX_MEDIA_BYTES)]
"""File size in bytes, from 1 to ``MAX_MEDIA_BYTES``."""

MEDIA_VERSION_MAX: Final = 2**31 - 1
MediaVersion = Annotated[int, Field(ge=1, le=MEDIA_VERSION_MAX)]
"""Optimistic-concurrency version: 1 when created, +1 per change with an effect.

The bound is the largest signed 32-bit integer so it fits a PostgreSQL ``integer``.
"""

CAMERA_MAX_LENGTH: Final = 120
CameraModel = Annotated[str, *safe_text(CAMERA_MAX_LENGTH)]
"""Camera make and model from EXIF, as safe single-line text of 1 to 120 characters."""

REASON_MAX_LENGTH: Final = 500
ModerationReason = Annotated[str, *safe_text(REASON_MAX_LENGTH)]
"""Why a moderator or the platform decided as it did: safe text, 1 to 500 characters."""


class ExifFacts(BaseModel):
    """What a file's EXIF metadata says, as read from the private original.

    The location can be a person's home or position and is **private**: it is never
    published, never put in an event and exists only on the original. The public
    copy is re-encoded without any EXIF block (see ``MediaAsset``).

    Implements: Value Object.

    Attributes:
        taken_at: Capture time with its precision, or ``None`` if absent.
        location: GPS position, or ``None`` if absent.
        camera: Camera make and model, or ``None`` if absent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    taken_at: DateWithPrecision | None = None
    location: Coordinates | None = None
    camera: CameraModel | None = None

    @property
    def is_empty(self) -> bool:
        """Tell whether the metadata says nothing at all.

        Returns:
            ``True`` if every fact is absent.
        """
        return self.taken_at is None and self.location is None and self.camera is None


class StoredFile(BaseModel):
    """What the platform established about an uploaded original once it arrived.

    Implements: Value Object.

    Attributes:
        sha256: Digest of the stored bytes, computed by the platform.
        byte_size: Size of the stored bytes.
        mime_type: Media type detected from the file's magic bytes.
        exif: EXIF facts read from the original, or ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: Sha256
    byte_size: ByteSize
    mime_type: MimeType
    exif: ExifFacts | None = None


class MediaAttribution(BaseModel):
    """Who a media asset comes from and what it belongs to.

    Implements: Value Object.

    Attributes:
        owner_id: The uploading user.
        source_id: The ``provenance`` source the asset is attributed to.
        report_id: The report the asset belongs to, or ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: EntityId
    source_id: EntityId
    report_id: EntityId | None = None


class UploadStatus(StrEnum):
    """Whether the file behind an asset has arrived in storage.

    Implements: Value Object.
    """

    REQUESTED = "requested"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanStatus(StrEnum):
    """The malware scanner's verdict on the original file.

    ``unavailable`` means the scanner could not run (for example the development
    ``NoOpScanner`` or an outage); it is not ``clean``, so it never allows
    publication.

    Implements: Value Object.
    """

    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
    UNAVAILABLE = "unavailable"


class ModerationStatus(StrEnum):
    """A human decision on whether an asset may be shown publicly.

    ``quarantined`` isolates an asset (for example after an infected scan or a
    complaint) until a moderator decides again.

    Implements: Value Object.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class SensitivityFlag(StrEnum):
    """Why an asset needs care before it is shown (**proposed** values, Q-M4).

    Set by a moderator only; nothing detects it automatically (Phase 3 plan §6).

    Implements: Value Object.
    """

    NONE = "none"
    INJURED_OR_DECEASED = "injured_or_deceased"
    IDENTIFIABLE_PEOPLE = "identifiable_people"
    OTHER = "other"


PUBLICATION_BLOCKING_SENSITIVITIES: Final = frozenset(
    {SensitivityFlag.INJURED_OR_DECEASED, SensitivityFlag.IDENTIFIABLE_PEOPLE}
)
"""Sensitivities that keep an asset from being published (**proposed**, Q-M5).

The cautious default: imagery of injured or dead people, or of identifiable people,
stays private even when approved, until the maintainer decides how it may be shown
(blurred, behind a warning, never).
"""


class Variant(StrEnum):
    """Which copy of an asset is meant.

    Implements: Value Object.
    """

    ORIGINAL = "original"
    PUBLIC = "public"


def _object_key(variant: Variant, asset_id: UUID) -> str:
    return f"media/{variant.value}/{asset_id}"


UPLOAD_KEY_PREFIX: Final = "media/upload/"
"""Prefix of the keys clients upload to; the only keys a client URL ever covers."""


def upload_object_key(asset_id: UUID) -> str:
    """Return the key an asset's file is uploaded to through a presigned ``PUT``.

    It is the only key a client is ever given a write URL for. A presigned URL
    stays valid until it expires, so the upload key can be overwritten after
    completion; completion therefore copies it to ``original_object_key``, which
    no client URL covers, and everything afterwards reads the original.

    Args:
        asset_id: The asset's id.

    Returns:
        ``media/upload/<id>``.
    """
    return f"{UPLOAD_KEY_PREFIX}{asset_id}"


def original_object_key(asset_id: UUID) -> str:
    """Return the storage key of an asset's private original.

    Only the platform writes it, by copying the upload at completion.

    Args:
        asset_id: The asset's id.

    Returns:
        ``media/original/<id>``.
    """
    return _object_key(Variant.ORIGINAL, asset_id)


def public_object_key(asset_id: UUID) -> str:
    """Return the storage key of an asset's EXIF-stripped public copy.

    Args:
        asset_id: The asset's id.

    Returns:
        ``media/public/<id>``.
    """
    return _object_key(Variant.PUBLIC, asset_id)
