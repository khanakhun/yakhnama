"""SQLAlchemy adapter of the ``ReportRepository`` port.

Writes go straight to the unit of work's transaction, so a later read in the same
unit of work sees them and a rollback discards them. Inserts run inside a savepoint
so a unique violation leaves the transaction usable. Rows are expunged as soon as
they are read or written, so a stale identity-map entry never shadows a later write.

A unique violation on insert is either a taken id (a client resending a report with
the same client id) or a second revision of the same report (``supersedes_id`` is
unique); both are conflicts. Optimistic concurrency: ``save`` updates a row only
``WHERE version = new version - 1``; if no row matches, the id is looked up once more
to tell a missing report (``ReportNotFoundError``) from a concurrent change
(``ConflictError``).

There is no delete: reports are never removed (``AGENTS.md`` §5).

Patterns: Repository (adapter side).
"""

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import ReportNotFoundError
from yakhnama.modules.reports.infrastructure.mappers import (
    report_to_row,
    report_to_values,
    row_to_report,
)
from yakhnama.modules.reports.infrastructure.orm import ReportRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyReportRepository:
    """PostgreSQL-backed implementation of ``ReportRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``ReportRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, report_id: EntityId) -> Report | None:
        """Return the report with ``report_id``, whatever its status.

        Args:
            report_id: The report's id.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(ReportRow).where(ReportRow.id == report_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_report(row)

    async def add(self, report: Report) -> None:
        """Insert a new report or a new revision.

        Args:
            report: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken, or another revision already
                supersedes the same report.
            sqlalchemy.exc.IntegrityError: If ``supersedes_id`` names a report that
                is not stored, which the application layer rules out.
        """
        row = report_to_row(report)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"report {report.id} or a revision of the same report exists"
            raise ConflictError(
                message, details={"report_id": str(report.id)}
            ) from error
        self._session.expunge(row)

    async def save(self, report: Report) -> None:
        """Update a stored report, checking optimistic concurrency.

        Args:
            report: The new state; its ``version`` is one more than the stored one.

        Raises:
            ReportNotFoundError: If no report with that id exists.
            ConflictError: If the stored version is not ``report.version - 1``.
        """
        statement = (
            update(ReportRow)
            .where(ReportRow.id == report.id, ReportRow.version == report.version - 1)
            .values(report_to_values(report))
            .returning(ReportRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is not None:
            return
        stored = await self._session.scalar(
            select(ReportRow.version).where(ReportRow.id == report.id)
        )
        if stored is None:
            raise ReportNotFoundError.for_id(report.id)
        message = (
            f"report {report.id} was changed concurrently "
            f"(expected version {report.version - 1})"
        )
        raise ConflictError(
            message,
            details={
                "report_id": str(report.id),
                "expected_version": report.version - 1,
                "stored_version": stored,
            },
        )
