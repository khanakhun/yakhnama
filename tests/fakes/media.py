"""In-memory fakes of the media ports.

The repository stages writes until the unit of work commits and enforces the
uniqueness and optimistic-concurrency rules of the SQL adapter. ``FakeStoragePort``
keeps objects in a dictionary per key and records every key it presigned or
copied; ``FakeExifReader`` and ``FakeMimeSniffer`` answer from what the test
arranges; ``FakeReportSourceLookup`` knows which user submitted which report.

Patterns: Fake.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.media.application.dto import (
    HttpHeader,
    MediaAssetRecord,
    PresignedDownload,
    PresignedUpload,
    StoredObject,
)
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.errors import MediaContentChangedError
from yakhnama.modules.media.domain.value_objects import (
    MAX_MEDIA_BYTES,
    ExifFacts,
    MimeType,
    UploadStatus,
)
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId

FAKE_STORAGE_EXPIRY = datetime(2026, 6, 1, 13, 0, tzinfo=UTC)
"""Expiry every presigned URL of ``FakeStoragePort`` carries."""


class InMemoryMediaAssetRepository:
    """``MediaAssetRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored assets, as a committed transaction left them.
    """

    def __init__(self, assets: Iterable[MediaAsset] = ()) -> None:
        """Create the repository.

        Args:
            assets: Assets that exist before the test acts.
        """
        self.committed: dict[EntityId, MediaAsset] = {
            asset.id: asset for asset in assets
        }
        self._staged: dict[EntityId, MediaAsset] = {}

    def _current(self) -> dict[EntityId, MediaAsset]:
        return {**self.committed, **self._staged}

    async def get(self, asset_id: EntityId) -> MediaAsset | None:
        """Return the asset, staged changes included.

        Args:
            asset_id: The asset's id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(asset_id)

    async def find_completed_by_digest(
        self, owner_id: EntityId, sha256: str
    ) -> MediaAsset | None:
        """Return the owner's oldest completed asset with the digest.

        Args:
            owner_id: The uploader.
            sha256: The digest.

        Returns:
            The asset, or ``None``.
        """
        matches = sorted(
            (
                asset
                for asset in self._current().values()
                if asset.owner_id == owner_id
                and asset.sha256 == sha256
                and asset.upload_status is UploadStatus.COMPLETED
            ),
            key=lambda asset: (asset.created_at, asset.id),
        )
        return matches[0] if matches else None

    async def add(self, asset: MediaAsset) -> None:
        """Stage a new asset.

        Args:
            asset: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        if asset.id in self._current():
            message = "the media asset already exists"
            raise ConflictError(message)
        self._staged[asset.id] = asset

    async def save(self, asset: MediaAsset) -> None:
        """Stage a changed asset.

        Args:
            asset: The new state.

        Raises:
            NotFoundError: If the asset is not stored.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(asset.id)
        if stored is None:
            message = f"media asset {asset.id} is not stored"
            raise NotFoundError(message)
        if asset.version != stored.version + 1:
            message = f"media asset {asset.id} was changed concurrently"
            raise ConflictError(message)
        self._staged[asset.id] = asset

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryMediaUnitOfWork(InMemoryUnitOfWork):
    """``MediaUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        media_assets: The asset repository bound to this unit of work.
    """

    def __init__(self, *, assets: Iterable[MediaAsset] = ()) -> None:
        """Create the unit of work.

        Args:
            assets: Assets that exist before the test acts.
        """
        super().__init__()
        self.media_assets = InMemoryMediaAssetRepository(assets)

    def _on_commit(self) -> None:
        self.media_assets.apply_staged()

    def _on_rollback(self) -> None:
        self.media_assets.discard_staged()


class InMemoryMediaQueryService:
    """``MediaQueryService`` reading a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryMediaUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def get_asset(self, asset_id: EntityId) -> MediaAssetRecord | None:
        """Return one committed asset.

        Args:
            asset_id: The asset.

        Returns:
            The record, or ``None``.
        """
        asset = self._uow.media_assets.committed.get(asset_id)
        return None if asset is None else MediaAssetRecord.from_entity(asset)

    async def list_assets(
        self, asset_ids: Sequence[EntityId]
    ) -> tuple[MediaAssetRecord, ...]:
        """Return the committed assets among ``asset_ids``, in order.

        Args:
            asset_ids: The assets.

        Returns:
            The records found.
        """
        committed = self._uow.media_assets.committed
        return tuple(
            MediaAssetRecord.from_entity(committed[asset_id])
            for asset_id in asset_ids
            if asset_id in committed
        )


class FakeStoragePort:
    """``StoragePort`` over a dictionary of objects, recording every key used.

    Implements: Fake (of Adapter).

    Attributes:
        objects: What ``head`` reports per key; tests put uploads here.
        presigned_puts: Keys presigned for upload, in order.
        presigned_gets: Keys presigned for download, in order.
        seals: ``(upload_key, original_key)`` pairs sealed, in order.
        copies: ``(original_key, public_key)`` pairs copied, in order.
        on_copy: Called after each copy, to simulate concurrent work.
    """

    def __init__(self, objects: Mapping[str, StoredObject] | None = None) -> None:
        """Create the storage.

        Args:
            objects: Objects that exist before the test acts.
        """
        self.objects: dict[str, StoredObject] = dict(objects or {})
        self.presigned_puts: list[str] = []
        self.presigned_gets: list[str] = []
        self.seals: list[tuple[str, str]] = []
        self.copies: list[tuple[str, str]] = []
        self.on_copy: Callable[[], None] | None = None

    async def presign_put(
        self, key: str, mime_type: MimeType, max_bytes: int
    ) -> PresignedUpload:
        """Record the key and return a fake upload URL.

        Args:
            key: The object key.
            mime_type: The declared media type.
            max_bytes: The size cap.

        Returns:
            A URL naming the key, with the content type and cap as headers.
        """
        self.presigned_puts.append(key)
        return PresignedUpload(
            url=f"https://storage.example.test/private/{key}?signature=put",
            headers=(
                HttpHeader(name="Content-Type", value=mime_type.value),
                HttpHeader(name="X-Max-Bytes", value=str(max_bytes)),
            ),
            expires_at=FAKE_STORAGE_EXPIRY,
        )

    async def head(self, key: str) -> StoredObject | None:
        """Return the arranged object at ``key``.

        Args:
            key: The object key.

        Returns:
            The object, or ``None``.
        """
        return self.objects.get(key)

    async def seal_upload(
        self, upload_key: str, original_key: str
    ) -> StoredObject | None:
        """Move the upload to the original key, as ``S3StoragePort`` copies it.

        Args:
            upload_key: Where the client uploaded.
            original_key: The original to write.

        Returns:
            The original; the upload itself if it is above the size cap (not
            moved); the original if only it exists; ``None`` if neither does.
        """
        upload = self.objects.get(upload_key)
        if upload is None:
            return self.objects.get(original_key)
        if upload.byte_size > MAX_MEDIA_BYTES:
            return upload
        self.seals.append((upload_key, original_key))
        self.objects[original_key] = self.objects.pop(upload_key)
        return self.objects[original_key]

    async def copy_stripped_public(
        self, original_key: str, public_key: str, *, expected_sha256: str
    ) -> None:
        """Record the copy and store a copy entry under ``public_key``.

        Args:
            original_key: The original.
            public_key: Where the copy goes.
            expected_sha256: The digest the original must still have.

        Raises:
            NotFoundError: If the original is not stored.
            MediaContentChangedError: If the stored digest differs.
        """
        original = self.objects.get(original_key)
        if original is None:
            message = f"no object at {original_key}"
            raise NotFoundError(message)
        if original.sha256 != expected_sha256:
            raise MediaContentChangedError.detected()
        self.copies.append((original_key, public_key))
        self.objects[public_key] = original
        if self.on_copy is not None:
            self.on_copy()

    async def presign_get(self, key: str) -> PresignedDownload:
        """Record the key and return a fake download URL.

        Args:
            key: The object key.

        Returns:
            A URL naming the key.
        """
        self.presigned_gets.append(key)
        return PresignedDownload(
            url=f"https://storage.example.test/{key}?signature=get",
            expires_at=FAKE_STORAGE_EXPIRY + timedelta(minutes=5),
        )


class FakeExifReader:
    """``ExifReader`` answering from arranged facts per key.

    Implements: Fake (of Adapter).

    Attributes:
        facts: The EXIF facts per key.
        reads: Keys read, in order.
    """

    def __init__(self, facts: Mapping[str, ExifFacts] | None = None) -> None:
        """Create the reader.

        Args:
            facts: The EXIF facts per key.
        """
        self.facts = dict(facts or {})
        self.reads: list[str] = []

    async def read(self, key: str) -> ExifFacts | None:
        """Return the arranged facts of ``key``.

        Args:
            key: The object key.

        Returns:
            The facts, or ``None``.
        """
        self.reads.append(key)
        return self.facts.get(key)


class FakeMimeSniffer:
    """``MimeSniffer`` answering from arranged types per key.

    Implements: Fake (of Adapter).

    Attributes:
        types: The detected type per key; a missing key is not an allowed type.
    """

    def __init__(self, types: Mapping[str, MimeType] | None = None) -> None:
        """Create the sniffer.

        Args:
            types: The detected type per key.
        """
        self.types = dict(types or {})

    async def sniff(self, key: str) -> MimeType | None:
        """Return the arranged type of ``key``.

        Args:
            key: The object key.

        Returns:
            The type, or ``None``.
        """
        return self.types.get(key)


class FakeReportSourceLookup:
    """``ReportSourceLookup`` answering from arranged reports.

    Implements: Fake (of Adapter).

    Attributes:
        reports: ``report_id -> (reporter_id, source_id)``.
    """

    def __init__(
        self, reports: Mapping[EntityId, tuple[EntityId, EntityId]] | None = None
    ) -> None:
        """Create the lookup.

        Args:
            reports: For each report, its reporter and source.
        """
        self.reports = dict(reports or {})

    async def find_source_for_reporter(
        self, report_id: EntityId, reporter_id: EntityId
    ) -> EntityId | None:
        """Return the report's source if ``reporter_id`` submitted it.

        Args:
            report_id: The report.
            reporter_id: The would-be uploader.

        Returns:
            The source id, or ``None``.
        """
        entry = self.reports.get(report_id)
        if entry is None or entry[0] != reporter_id:
            return None
        return entry[1]
