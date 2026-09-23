"""Factories for the ``media`` domain: media assets awaiting upload.

``MediaAssetTestFactory`` is suffixed ``TestFactory`` because the domain already has a
``MediaAssetFactory`` (``yakhnama.modules.media.domain.factories``). It builds an
asset in ``requested`` state, as ``MediaAssetFactory.request_upload`` does, directly
for arranging state; tests reach later states through the aggregate's own methods so
every invariant is exercised on the way. ``stored_file`` returns a synthetic
``StoredFile`` whose digest is a hash of a counter, never of a real file.

Patterns: Factory.
"""

import hashlib
import itertools
from collections.abc import Mapping
from uuid import UUID

from polyfactory import PostGenerated, Use

from tests.factories.base import FACTORY_IDS, YakhnamaModelFactory, random_instant
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.value_objects import (
    ExifFacts,
    MimeType,
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
    StoredFile,
    UploadStatus,
    original_object_key,
)

_DIGEST_COUNTER = itertools.count(1)


def _original_key(_name: str, values: Mapping[str, object]) -> object:
    asset_id = values["id"]
    assert isinstance(asset_id, UUID)
    return original_object_key(asset_id)


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    return values["created_at"]


def synthetic_sha256() -> str:
    """Return a distinct, synthetic SHA-256 digest on every call.

    Returns:
        64 lower-case hexadecimal digits.
    """
    return hashlib.sha256(f"test-file-{next(_DIGEST_COUNTER)}".encode()).hexdigest()


def stored_file(
    *,
    sha256: str | None = None,
    byte_size: int = 1024,
    mime_type: MimeType = MimeType.JPEG,
    exif: ExifFacts | None = None,
) -> StoredFile:
    """Return the facts of a synthetic stored original.

    Args:
        sha256: The digest, or ``None`` for a fresh synthetic one.
        byte_size: The size in bytes.
        mime_type: The detected media type.
        exif: EXIF facts, or ``None``.

    Returns:
        The stored-file value object.
    """
    return StoredFile(
        sha256=synthetic_sha256() if sha256 is None else sha256,
        byte_size=byte_size,
        mime_type=mime_type,
        exif=exif,
    )


class MediaAssetTestFactory(YakhnamaModelFactory[MediaAsset]):
    """Builds JPEG assets in ``requested`` state at version 1.

    Implements: Factory.
    """

    __model__ = MediaAsset

    id = Use(FACTORY_IDS.new_id)
    owner_id = Use(FACTORY_IDS.new_id)
    report_id = None
    source_id = Use(FACTORY_IDS.new_id)
    original_key = PostGenerated(_original_key)
    public_key = None
    sha256 = None
    mime_type = MimeType.JPEG
    byte_size = None
    exif = None
    upload_status = UploadStatus.REQUESTED
    scan_status = ScanStatus.PENDING
    moderation_status = ModerationStatus.PENDING
    sensitivity = SensitivityFlag.NONE
    moderation_reason = None
    version = 1
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
