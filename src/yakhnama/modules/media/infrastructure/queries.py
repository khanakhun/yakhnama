"""SQL implementation of the ``MediaQueryService`` port.

Each query opens its own short read session. ``list_assets`` loads every listed id
in one ``IN`` query and returns the records in the caller's order, skipping ids that
do not exist, as the port requires; a repeated id is returned once per occurrence,
as the in-memory fake does.

**The moderators' queue** (``list_queue``) pages completed uploads oldest first by
keyset on ``(created_at, id)``, served by the partial index
``ix_media_assets_moderation_queue``. An asset uploaded before its report existed
(``POST /media``) keeps ``report_id`` ``NULL``, so the queue resolves the report with
``coalesce(media_assets.report_id, <newest report revision listing the asset>)``: a
correlated scalar subquery on ``reports`` whose ``media_ids`` JSONB array contains
the asset id as a string (``@>``, which the GIN index ``ix_reports_media_ids_gin``
with ``jsonb_path_ops`` serves, migration 0025). The reports table is referenced as
a lightweight ``table()`` clause with only the columns read here, never through the
reports module's ORM model: modules share no Python internals (``AGENTS.md`` §2.1),
the same precedent as the events module's read of ``verification_cases``.
``get_asset`` keeps returning the stored ``report_id``.

Patterns: Query Service (adapter side).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Text,
    Uuid,
    and_,
    cast,
    column,
    func,
    or_,
    select,
    table,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.media.application.dto import MediaAssetRecord
from yakhnama.modules.media.domain.value_objects import (
    ModerationStatus,
    ScanStatus,
    UploadStatus,
)
from yakhnama.modules.media.infrastructure.mappers import row_to_asset
from yakhnama.modules.media.infrastructure.orm import MediaAssetRow
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)

REPORTS: Final = table(
    "reports",
    column("id", Uuid()),
    column("media_ids", JSONB()),
    column("created_at", DateTime(timezone=True)),
)
"""The columns of the reports module's table the queue reads."""


def report_of_asset() -> ColumnElement[EntityId | None]:
    """Return the asset's report: its own, or the newest revision listing it.

    Returns:
        ``coalesce(report_id, (newest reports.id whose media_ids contains the
        asset id))``, correlated to ``media_assets``.
    """
    reports = REPORTS
    listing = (
        select(reports.c.id)
        .where(
            reports.c.media_ids.op("@>")(
                func.jsonb_build_array(cast(MediaAssetRow.id, Text))
            )
        )
        .order_by(reports.c.created_at.desc(), reports.c.id.desc())
        .limit(1)
        .correlate(MediaAssetRow)
        .scalar_subquery()
    )
    return func.coalesce(MediaAssetRow.report_id, listing)


def decode_since(sort_key: str) -> datetime:
    """Parse the ``created_at`` instant a queue cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``.

    Returns:
        The timezone-aware instant.

    Raises:
        ValidationError: If ``sort_key`` is not an ISO 8601 instant with an offset.
    """
    try:
        since = datetime.fromisoformat(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    # Comparing a naive instant with timestamptz would assume the session zone.
    if since.utcoffset() is None:
        raise _invalid_cursor()
    return since


def _queue_record(row: MediaAssetRow, report_id: EntityId | None) -> MediaAssetRecord:
    record = MediaAssetRecord.from_entity(row_to_asset(row))
    # Validated again rather than model_copy, which would skip validation.
    return MediaAssetRecord.model_validate(
        {**record.model_dump(), "report_id": report_id}
    )


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


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

    async def list_queue(
        self,
        *,
        moderation_status: ModerationStatus | None,
        scan_status: ScanStatus | None,
        page: PageRequest,
    ) -> Page[MediaAssetRecord]:
        """Return one page of completed uploads, oldest first, report resolved.

        Args:
            moderation_status: Only assets with this status, if set.
            scan_status: Only assets with this scan verdict, if set.
            page: Page size and cursor.

        Returns:
            Up to ``page.limit`` records and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = page.decode_cursor()
        statement = (
            select(MediaAssetRow, report_of_asset())
            .where(MediaAssetRow.upload_status == UploadStatus.COMPLETED.value)
            .order_by(MediaAssetRow.created_at, MediaAssetRow.id)
            # One extra row tells whether another page follows.
            .limit(page.limit + 1)
        )
        if moderation_status is not None:
            statement = statement.where(
                MediaAssetRow.moderation_status == moderation_status.value
            )
        if scan_status is not None:
            statement = statement.where(MediaAssetRow.scan_status == scan_status.value)
        if cursor is not None:
            since = decode_since(cursor.sort_key)
            statement = statement.where(
                or_(
                    MediaAssetRow.created_at > since,
                    and_(
                        MediaAssetRow.created_at == since,
                        MediaAssetRow.id > cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).tuples().all()
        records = [
            (_queue_record(row, report_id), row.created_at) for row, report_id in rows
        ]
        window = records[: page.limit]
        next_cursor = None
        if len(records) > page.limit:
            last, created_at = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=created_at.isoformat(), last_id=last.id)
            )
        return Page[MediaAssetRecord](
            items=tuple(record for record, _ in window), next_cursor=next_cursor
        )
