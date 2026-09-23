"""SQL implementation of the ``AuditQueryService`` port.

Each query opens its own short read session and pages by keyset on
``(occurred_at, id)`` descending, newest first: the next page holds rows with
``(occurred_at, id) < (since, last_id)``. The cursor's ``sort_key`` is
``occurred_at`` in ISO 8601 and its ``last_id`` the last entry's id, as the port
requires. Both listings are served by an index ending in ``(occurred_at, id)``.

Patterns: Query Service (adapter side).
"""

from datetime import datetime

from sqlalchemy import ColumnElement, and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.audit.application.dto import AuditEntrySummary
from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.modules.audit.infrastructure.mappers import row_to_entry
from yakhnama.modules.audit.infrastructure.orm import AuditEntryRow
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)


def decode_since(sort_key: str) -> datetime:
    """Parse the ``occurred_at`` instant an audit-listing cursor carries.

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


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


class SqlAlchemyAuditQueryService:
    """PostgreSQL-backed implementation of ``AuditQueryService``.

    Implements: Query Service (port ``AuditQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def list_for_target(
        self, target: AuditTarget, page: PageRequest
    ) -> Page[AuditEntrySummary]:
        """Return one page of the entries about ``target``, newest first.

        Args:
            target: The aggregate type and id.
            page: Page size and cursor.

        Returns:
            The page; empty if nothing about the target was recorded.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        condition = (AuditEntryRow.target_type == target.target_type) & (
            AuditEntryRow.target_id == target.target_id
        )
        return await self._page(condition, page)

    async def list_recent(self, page: PageRequest) -> Page[AuditEntrySummary]:
        """Return one page of the whole log, newest first.

        Args:
            page: Page size and cursor.

        Returns:
            The page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        return await self._page(true(), page)

    async def _page(
        self, condition: ColumnElement[bool], page: PageRequest
    ) -> Page[AuditEntrySummary]:
        cursor = page.decode_cursor()
        statement = (
            select(AuditEntryRow)
            .where(condition)
            .order_by(AuditEntryRow.occurred_at.desc(), AuditEntryRow.id.desc())
            # One extra row tells whether another page follows.
            .limit(page.limit + 1)
        )
        if cursor is not None:
            since = decode_since(cursor.sort_key)
            statement = statement.where(
                or_(
                    AuditEntryRow.occurred_at < since,
                    and_(
                        AuditEntryRow.occurred_at == since,
                        AuditEntryRow.id < cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        summaries = [AuditEntrySummary.from_entity(row_to_entry(row)) for row in rows]
        window = summaries[: page.limit]
        next_cursor = None
        if len(summaries) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.occurred_at.isoformat(), last_id=last.id)
            )
        return Page[AuditEntrySummary](items=tuple(window), next_cursor=next_cursor)
