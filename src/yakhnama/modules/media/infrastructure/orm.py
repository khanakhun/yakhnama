"""SQLAlchemy row model of the ``media`` bounded context.

Rows are persistence shapes only (ADR 0004): ``mappers.py`` translates them to and
from the frozen ``MediaAsset`` aggregate, and nothing outside this package sees them.

The EXIF facts of an original are split over two columns: ``exif`` is a JSONB object
with the capture time (``taken_at`` as ``DateWithPrecision``) and the camera, and
``exif_location`` the EXIF GPS position as a WGS84 point (EPSG:4326, ADR 0002).
``exif`` is ``NULL`` exactly when the original had no readable EXIF block, and a
check constraint forbids a location without it. The location is **private**, like
the reporter's own position: it is never published.

``(owner_id, sha256)`` is indexed only for completed uploads (a partial index),
which is exactly what ``find_completed_by_digest`` searches. It is not unique: the
application deduplicates by returning the oldest completed asset, and a race between
two completions of the same file is harmless.

Patterns: Adapter (ORM row models behind the repository and query service adapters).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from geoalchemy2 import Geometry, WKBElement
from sqlalchemy import CheckConstraint, Index, Integer, String
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base

WGS84_SRID: Final = 4326
"""EPSG code of every stored geometry (ADR 0002)."""

MEDIA_ASSETS_TABLE: Final = "media_assets"


class MediaAssetRow(Base):
    """Row model of the ``media_assets`` table: one row per ``MediaAsset``.

    ``owner_id``, ``report_id`` and ``source_id`` carry no foreign key: they belong
    to the ``identity``, ``reports`` and ``provenance`` modules, which this module
    knows only through their facades.

    Implements: Adapter (ORM row model of ``SqlAlchemyMediaAssetRepository``).

    Attributes:
        id: Primary key, the asset id (UUIDv7).
        owner_id: The uploading user.
        report_id: The report the asset belongs to, if any.
        source_id: The provenance source the asset is attributed to.
        original_key: Storage key of the private original.
        public_key: Storage key of the EXIF-stripped public copy, once published.
        sha256: Digest of the original, once the upload completed.
        mime_type: ``MimeType`` value.
        byte_size: Size of the original in bytes, once the upload completed.
        exif: Capture time and camera from EXIF, if the original had EXIF.
        exif_location: EXIF GPS position, if any; private.
        upload_status: ``UploadStatus`` value.
        scan_status: ``ScanStatus`` value.
        moderation_status: ``ModerationStatus`` value.
        sensitivity: ``SensitivityFlag`` value.
        moderation_reason: Why the asset was moderated as it was, if recorded.
        version: Optimistic-concurrency version, compared on every update.
        created_at: When the asset was requested, UTC.
        updated_at: When the asset last changed, UTC.
    """

    __tablename__ = MEDIA_ASSETS_TABLE
    __table_args__ = (
        CheckConstraint(
            "exif_location IS NULL OR exif IS NOT NULL",
            name="exif_location_needs_exif",
        ),
        Index(
            "ix_media_assets_owner_id_sha256_completed",
            "owner_id",
            "sha256",
            postgresql_where=sql_text("upload_status = 'completed'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID]
    report_id: Mapped[UUID | None] = mapped_column(index=True)
    source_id: Mapped[UUID]
    # Lengths follow the domain bounds: OBJECT_KEY_PATTERN (at most 256), a
    # SHA-256 in hexadecimal, the longest MimeType value, CAMERA_MAX_LENGTH and
    # REASON_MAX_LENGTH.
    original_key: Mapped[str] = mapped_column(String(256))
    public_key: Mapped[str | None] = mapped_column(String(256))
    sha256: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    exif: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    exif_location: Mapped[WKBElement | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=WGS84_SRID, spatial_index=False)
    )
    upload_status: Mapped[str] = mapped_column(String(16))
    scan_status: Mapped[str] = mapped_column(String(16))
    moderation_status: Mapped[str] = mapped_column(String(16))
    sensitivity: Mapped[str] = mapped_column(String(32))
    moderation_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
