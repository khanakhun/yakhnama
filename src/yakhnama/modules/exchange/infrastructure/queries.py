"""SQL implementation of the ``ExchangeQueryService`` port.

The port returns every job it is asked for: who may see which job is decided by
``ExchangeJobQueryService`` in the application layer, never here.

**Order and paging.** Export jobs are listed newest request first by keyset on
``(requested_at, id)`` descending; the cursor's ``sort_key`` is ``requested_at`` in
ISO 8601 (UTC) and ``last_id`` the last job's id, exactly as the Fake builds it.
``ix_export_jobs_requested_by_requested_at`` serves a user's own listing and
``ix_export_jobs_requested_at_id`` the moderators' listing of every job.

Patterns: Query Service (adapter side).
"""

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.modules.exchange.application.dto import (
    ExportJobDetail,
    ExportJobSummary,
    ImportJobDetail,
)
from yakhnama.modules.exchange.infrastructure.mappers import (
    row_to_export_job,
    row_to_import_job,
)
from yakhnama.modules.exchange.infrastructure.orm import ExportJobRow, ImportJobRow
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)


def decode_requested_at(sort_key: str) -> datetime:
    """Parse the request instant an export-job cursor carries.

    Args:
        sort_key: The cursor's ``sort_key``, untrusted client input.

    Returns:
        The timezone-aware instant.

    Raises:
        ValidationError: If ``sort_key`` is not an ISO 8601 instant with an offset.
    """
    try:
        instant = datetime.fromisoformat(sort_key)
    except ValueError as error:
        raise _invalid_cursor() from error
    # Comparing a naive instant with timestamptz would assume the session zone.
    if instant.utcoffset() is None:
        raise _invalid_cursor()
    return instant


def _invalid_cursor() -> ValidationError:
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


class SqlAlchemyExchangeQueryService:
    """PostgreSQL-backed ``ExchangeQueryService``.

    Opens one read session per query and returns DTOs only.

    Implements: Query Service (port ``ExchangeQueryService``).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Create the query service.

        Args:
            session_factory: Opens one read session per query.
        """
        self._session_factory = session_factory

    async def get_export_job(self, job_id: EntityId) -> ExportJobDetail | None:
        """Return one export job.

        Args:
            job_id: The job.

        Returns:
            Its detail view, or ``None``.
        """
        async with self._session_factory() as session:
            row = await session.get(ExportJobRow, job_id)
        return (
            None if row is None else ExportJobDetail.from_entity(row_to_export_job(row))
        )

    async def list_export_jobs(
        self, requested_by: EntityId | None, page: PageRequest
    ) -> Page[ExportJobSummary]:
        """Return one page of export jobs, newest request first, then by id.

        Args:
            requested_by: Only this user's jobs, or every job when ``None``.
            page: Page size and cursor.

        Returns:
            The page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = page.decode_cursor()
        statement = (
            select(ExportJobRow)
            .order_by(ExportJobRow.requested_at.desc(), ExportJobRow.id.desc())
            # One extra row tells whether another page follows.
            .limit(page.limit + 1)
        )
        if requested_by is not None:
            statement = statement.where(ExportJobRow.requested_by == requested_by)
        if cursor is not None:
            since = decode_requested_at(cursor.sort_key)
            statement = statement.where(
                or_(
                    ExportJobRow.requested_at < since,
                    and_(
                        ExportJobRow.requested_at == since,
                        ExportJobRow.id < cursor.last_id,
                    ),
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        window = rows[: page.limit]
        next_cursor = None
        if len(rows) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.requested_at.isoformat(), last_id=last.id)
            )
        return Page[ExportJobSummary](
            items=tuple(
                ExportJobSummary.from_entity(row_to_export_job(row)) for row in window
            ),
            next_cursor=next_cursor,
        )

    async def get_import_job(self, job_id: EntityId) -> ImportJobDetail | None:
        """Return one import job.

        Args:
            job_id: The job.

        Returns:
            Its detail view, or ``None``.
        """
        async with self._session_factory() as session:
            row = await session.get(ImportJobRow, job_id)
        return (
            None if row is None else ImportJobDetail.from_entity(row_to_import_job(row))
        )
