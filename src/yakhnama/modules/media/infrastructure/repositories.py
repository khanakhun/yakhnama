"""SQLAlchemy adapter of the ``MediaAssetRepository`` port.

Writes go straight to the unit of work's transaction, so a later read in the same
unit of work sees them and a rollback discards them. Inserts run inside a savepoint
so a unique violation leaves the transaction usable. Rows are expunged as soon as
they are read or written, so a stale identity-map entry never shadows a later write.

Optimistic concurrency: ``save`` updates a row only ``WHERE version = new version -
1``; if no row matches, the id is looked up once more to tell a missing asset
(``MediaAssetNotFoundError``) from a concurrent change (``ConflictError``).

Patterns: Repository (adapter side).
"""

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.errors import MediaAssetNotFoundError
from yakhnama.modules.media.domain.value_objects import UploadStatus
from yakhnama.modules.media.infrastructure.mappers import (
    asset_to_row,
    asset_to_values,
    row_to_asset,
)
from yakhnama.modules.media.infrastructure.orm import MediaAssetRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyMediaAssetRepository:
    """PostgreSQL-backed implementation of ``MediaAssetRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``MediaAssetRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, asset_id: EntityId) -> MediaAsset | None:
        """Return the asset with ``asset_id``, whatever its status.

        Args:
            asset_id: The asset's id.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._load_first(MediaAssetRow.id == asset_id)

    async def find_completed_by_digest(
        self, owner_id: EntityId, sha256: str
    ) -> MediaAsset | None:
        """Return ``owner_id``'s oldest completed asset with digest ``sha256``.

        Served by the partial index on ``(owner_id, sha256)`` for completed
        uploads; ties on ``created_at`` fall back to the id.

        Args:
            owner_id: The uploader.
            sha256: The digest, 64 lower-case hexadecimal digits.

        Returns:
            The oldest such asset, or ``None``.
        """
        return await self._load_first(
            (MediaAssetRow.owner_id == owner_id)
            & (MediaAssetRow.sha256 == sha256)
            & (MediaAssetRow.upload_status == UploadStatus.COMPLETED.value)
        )

    async def add(self, asset: MediaAsset) -> None:
        """Insert a new asset.

        Args:
            asset: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        row = asset_to_row(asset)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"media asset {asset.id} already exists"
            raise ConflictError(message, details={"asset_id": str(asset.id)}) from error
        self._session.expunge(row)

    async def save(self, asset: MediaAsset) -> None:
        """Update a stored asset, checking optimistic concurrency.

        Args:
            asset: The new state; its ``version`` is one more than the stored one.

        Raises:
            MediaAssetNotFoundError: If no asset with that id exists.
            ConflictError: If the stored version is not ``asset.version - 1``.
        """
        statement = (
            update(MediaAssetRow)
            .where(
                MediaAssetRow.id == asset.id,
                MediaAssetRow.version == asset.version - 1,
            )
            .values(asset_to_values(asset))
            .returning(MediaAssetRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is not None:
            return
        stored = await self._session.scalar(
            select(MediaAssetRow.version).where(MediaAssetRow.id == asset.id)
        )
        if stored is None:
            raise MediaAssetNotFoundError.for_id(asset.id)
        message = (
            f"media asset {asset.id} was changed concurrently "
            f"(expected version {asset.version - 1})"
        )
        raise ConflictError(
            message,
            details={
                "asset_id": str(asset.id),
                "expected_version": asset.version - 1,
                "stored_version": stored,
            },
        )

    async def _load_first(self, condition: ColumnElement[bool]) -> MediaAsset | None:
        statement = (
            select(MediaAssetRow)
            .where(condition)
            .order_by(MediaAssetRow.created_at, MediaAssetRow.id)
            .limit(1)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_asset(row)
