"""SQL implementation of the ``MediaQueryService`` port.

Each query opens its own short read session. ``list_assets`` loads every listed id
in one ``IN`` query and returns the records in the caller's order, skipping ids that
do not exist, as the port requires; a repeated id is returned once per occurrence,
as the in-memory fake does.

Patterns: Query Service (adapter side).
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.media.application.dto import MediaAssetRecord
from yakhnama.modules.media.infrastructure.mappers import row_to_asset
from yakhnama.modules.media.infrastructure.orm import MediaAssetRow
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyMediaQueryService:
    """PostgreSQL-backed implementation of ``MediaQueryService``.

    Implements: Query Service (port ``MediaQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def get_asset(self, asset_id: EntityId) -> MediaAssetRecord | None:
        """Return one asset.

        Args:
            asset_id: The asset.

        Returns:
            The record, or ``None``.
        """
        async with self._session_factory() as session:
            row = await session.get(MediaAssetRow, asset_id)
        return None if row is None else MediaAssetRecord.from_entity(row_to_asset(row))

    async def list_assets(
        self, asset_ids: Sequence[EntityId]
    ) -> tuple[MediaAssetRecord, ...]:
        """Return the listed assets that exist, in ``asset_ids`` order.

        Args:
            asset_ids: The assets, at most a report's worth.

        Returns:
            The records found.
        """
        if not asset_ids:
            return ()
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MediaAssetRow).where(MediaAssetRow.id.in_(set(asset_ids)))
                )
            ).scalars()
            found = {
                row.id: MediaAssetRecord.from_entity(row_to_asset(row)) for row in rows
            }
        return tuple(found[asset_id] for asset_id in asset_ids if asset_id in found)
