"""Ports the media application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1): ``StoragePort`` to the S3/MinIO adapter, ``ExifReader``
and ``MimeSniffer`` to the Pillow and ``filetype`` adapters, ``ReportSourceLookup``
to the reports facade. Sources are registered and cited through
``provenance.public``; scans are scheduled through the kernel's ``TaskQueue``.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from collections.abc import Sequence
from typing import Final, Protocol

from yakhnama.modules.media.application.dto import (
    MediaAssetRecord,
    PresignedDownload,
    PresignedUpload,
    StoredObject,
)
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.value_objects import ExifFacts, MimeType
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

SCAN_TASK: Final = "media.scan"
"""Task name of the malware scan; payload ``{asset_id}``. The worker scans the
original and sends ``RecordScanResult``."""


class MediaAssetRepository(Protocol):
    """Loads and stages ``MediaAsset`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, asset_id: EntityId) -> MediaAsset | None:
        """Return the asset with ``asset_id``, whatever its status.

        Args:
            asset_id: The asset's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def find_completed_by_digest(
        self, owner_id: EntityId, sha256: str
    ) -> MediaAsset | None:
        """Return ``owner_id``'s completed asset with digest ``sha256``, if any.

        Args:
            owner_id: The uploader.
            sha256: The digest, 64 lower-case hexadecimal digits.

        Returns:
            The oldest such asset, or ``None``.
        """
        ...

    async def add(self, asset: MediaAsset) -> None:
        """Stage a new asset.

        Args:
            asset: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        ...

    async def save(self, asset: MediaAsset) -> None:
        """Stage a changed asset, checking optimistic concurrency.

        Args:
            asset: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If no asset with that id exists.
            ConflictError: If the stored version is not ``asset.version - 1``.
        """
        ...


class MediaUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the media repository.

    Implements: Unit of Work.
    """

    @property
    def media_assets(self) -> MediaAssetRepository:
        """Return the media asset repository bound to this transaction."""
        ...


type MediaUnitOfWorkFactory = UnitOfWorkFactory[MediaUnitOfWork]
"""Opens a fresh media unit of work per use case."""


class MediaQueryService(Protocol):
    """Read port for assets; returns internal records.

    Authorisation is applied by ``AuthorisedMediaQueryService``, or by the
    composition-root adapters built on it for the reports module.

    Implements: Query Service.
    """

    async def get_asset(self, asset_id: EntityId) -> MediaAssetRecord | None:
        """Return one asset.

        Args:
            asset_id: The asset.

        Returns:
            The record, or ``None``.
        """
        ...

    async def list_assets(
        self, asset_ids: Sequence[EntityId]
    ) -> tuple[MediaAssetRecord, ...]:
        """Return the listed assets that exist, in ``asset_ids`` order.

        Args:
            asset_ids: The assets, at most a report's worth.

        Returns:
            The records found.
        """
        ...


class StoragePort(Protocol):
    """Object storage with a private bucket for originals and a public one.

    Implements: Adapter (port side).
    """

    async def presign_put(
        self, key: str, mime_type: MimeType, max_bytes: int
    ) -> PresignedUpload:
        """Return a presigned upload for ``key`` in the private bucket.

        The URL is bound to the content type and caps the size at ``max_bytes``
        (for example with a presigned POST policy or a signed content-length
        range), so storage itself refuses a larger file.

        Args:
            key: The original's object key.
            mime_type: The declared media type.
            max_bytes: Largest accepted file.

        Returns:
            The URL, required headers and expiry.
        """
        ...

    async def head(self, key: str) -> StoredObject | None:
        """Return what storage knows about ``key`` in the private bucket.

        Args:
            key: The original's object key.

        Returns:
            The digest, size and stored content type, or ``None`` if absent.
        """
        ...

    async def copy_stripped_public(self, original_key: str, public_key: str) -> None:
        """Write an EXIF-free re-encoding of the original to the public bucket.

        Idempotent: writing the same key again replaces the copy.

        Args:
            original_key: The private original.
            public_key: Where the public copy goes.
        """
        ...

    async def presign_get(self, key: str) -> PresignedDownload:
        """Return a presigned download of ``key``, in whichever bucket holds it.

        Args:
            key: An original or public object key.

        Returns:
            The URL and expiry.
        """
        ...


class ExifReader(Protocol):
    """Reads EXIF facts from a stored original.

    Implements: Adapter (port side).
    """

    async def read(self, key: str) -> ExifFacts | None:
        """Return the EXIF facts of the original at ``key``.

        Args:
            key: The original's object key.

        Returns:
            The facts, or ``None`` if the file has no readable EXIF block.
        """
        ...


class MimeSniffer(Protocol):
    """Detects a stored file's media type from its magic bytes.

    Implements: Adapter (port side).
    """

    async def sniff(self, key: str) -> MimeType | None:
        """Return the media type of the original at ``key``.

        Args:
            key: The original's object key.

        Returns:
            The detected type, or ``None`` if it is not an allowed type.
        """
        ...


class ReportSourceLookup(Protocol):
    """Finds the source of a user's own report, from the reports module.

    Implements: Adapter (port side).
    """

    async def find_source_for_reporter(
        self, report_id: EntityId, reporter_id: EntityId
    ) -> EntityId | None:
        """Return the report's source id if ``reporter_id`` submitted the report.

        Args:
            report_id: The report.
            reporter_id: The would-be uploader.

        Returns:
            The report's ``source_id``, or ``None`` if the report does not exist
            or is someone else's.
        """
        ...
