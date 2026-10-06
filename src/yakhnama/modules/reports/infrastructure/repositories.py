"""SQLAlchemy adapters of the ``ReportRepository`` and guest submission ports.

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

``add`` also fills ``lineage_id`` (ADR 0022): a report's own id for revision 1, and
for a later revision the lineage of the report it supersedes, read in the same
transaction. ``SqlAlchemyReportReviewRepository`` writes a lineage's review row and
appends its last mark to ``report_review_marks`` in the same transaction, review
first because the mark references it; marks are only ever inserted.

``SqlAlchemyGuestSubmissionRepository`` follows the same rules for guest submissions
(a unique violation on ``reference`` or ``report_id`` is a conflict). Its
``lock_cap`` takes a transaction-scoped PostgreSQL advisory lock with one constant
key per hourly cap (``pg_advisory_xact_lock``), released when the unit of work
commits or rolls back, so cap checks of concurrent transactions run one after the
other. ``SqlAlchemySpentChallengeRepository`` inserts one row per redeemed
challenge, whose primary key turns a replay into ``GuestChallengeSpentError`` even
when two redemptions race; the insert itself refuses a challenge that has expired
by the database's clock, the clock the purge uses as well.

Patterns: Repository (adapter side).
"""

from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import bindparam, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.reports.application.dto import GuestSubmissionWindow
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import (
    GuestChallengeExpiredError,
    GuestChallengeSpentError,
    ReportNotFoundError,
)
from yakhnama.modules.reports.domain.guest_submissions import (
    GuestCap,
    GuestSubmission,
)
from yakhnama.modules.reports.domain.reviews import ReportReview
from yakhnama.modules.reports.infrastructure.mappers import (
    guest_submission_to_values,
    report_review_to_values,
    report_to_row,
    report_to_values,
    review_mark_to_row,
    row_to_guest_submission,
    row_to_report,
    rows_to_report_review,
)
from yakhnama.modules.reports.infrastructure.orm import (
    GuestChallengeRow,
    GuestSubmissionRow,
    ReportReviewMarkRow,
    ReportReviewRow,
    ReportRow,
)
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId

# Advisory lock keys of the hourly caps: constants, unique in this database, chosen
# far from small integers another feature might pick ("YKGO", "YKGR" in ASCII).
CAP_LOCK_KEYS: Final[dict[GuestCap, int]] = {
    GuestCap.OPENED: 0x594B474F,
    GuestCap.REPORTS: 0x594B4752,
}

_SPEND_CHALLENGE: Final = text(
    "INSERT INTO guest_challenges (salt, expires_at) "
    "SELECT :salt, :expires_at WHERE :expires_at > now() "
    "RETURNING salt"
).bindparams(bindparam("expires_at", type_=GuestChallengeRow.expires_at.type))

_PURGE_CHALLENGES: Final = text(
    "DELETE FROM guest_challenges "
    "WHERE expires_at < now() - make_interval(secs => :grace_seconds) "
    "RETURNING salt"
)


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
        row.lineage_id = await self._lineage_for(report)
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

    async def lineage_of(self, report_id: EntityId) -> EntityId | None:
        """Return the lineage of a stored report: the id of its revision 1.

        Args:
            report_id: Any revision.

        Returns:
            The lineage id, or ``None`` if the report is not stored.
        """
        lineage_id: EntityId | None = await self._session.scalar(
            select(ReportRow.lineage_id).where(ReportRow.id == report_id)
        )
        return lineage_id

    async def _lineage_for(self, report: Report) -> EntityId:
        if report.supersedes_id is None:
            return report.id
        lineage_id = await self.lineage_of(report.supersedes_id)
        # For a revision of an unstored report there is no lineage to inherit; the
        # superseded id stands in, and the insert fails on the foreign key of
        # supersedes_id with the IntegrityError ``add`` documents.
        return report.supersedes_id if lineage_id is None else lineage_id


class SqlAlchemyReportReviewRepository:
    """PostgreSQL-backed implementation of ``ReportReviewRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``ReportReviewRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, lineage_id: EntityId) -> ReportReview | None:
        """Return the review of a lineage with its last mark.

        Args:
            lineage_id: The id of the lineage's revision 1.

        Returns:
            The aggregate, or ``None`` if the lineage was never marked.
        """
        statement = (
            select(ReportReviewRow, ReportReviewMarkRow)
            .join(
                ReportReviewMarkRow,
                ReportReviewMarkRow.id == ReportReviewRow.last_mark_id,
            )
            .where(ReportReviewRow.lineage_id == lineage_id)
        )
        found = (await self._session.execute(statement)).tuples().one_or_none()
        if found is None:
            return None
        review_row, mark_row = found
        self._session.expunge(review_row)
        self._session.expunge(mark_row)
        return rows_to_report_review(review_row, mark_row)

    async def add(self, review: ReportReview) -> None:
        """Insert a lineage's first review and append its first mark.

        Args:
            review: The new aggregate at version 1.

        Raises:
            ConflictError: If the lineage already has a review.
        """
        row = ReportReviewRow(lineage_id=review.id, **report_review_to_values(review))
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"report lineage {review.id} was marked concurrently"
            raise ConflictError(
                message, details={"lineage_id": str(review.id)}
            ) from error
        self._session.expunge(row)
        await self._append(review)

    async def save(self, review: ReportReview) -> None:
        """Update a stored review and append its new last mark.

        Args:
            review: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If the lineage has no stored review.
            ConflictError: If the stored version is not ``review.version - 1``.
        """
        statement = (
            update(ReportReviewRow)
            .where(
                ReportReviewRow.lineage_id == review.id,
                ReportReviewRow.version == review.version - 1,
            )
            .values(report_review_to_values(review))
            .returning(ReportReviewRow.lineage_id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is None:
            stored = await self._session.scalar(
                select(ReportReviewRow.version).where(
                    ReportReviewRow.lineage_id == review.id
                )
            )
            if stored is None:
                message = f"report lineage {review.id} has no review"
                raise NotFoundError(message, details={"lineage_id": str(review.id)})
            message = (
                f"report lineage {review.id} was marked concurrently "
                f"(expected version {review.version - 1})"
            )
            raise ConflictError(
                message,
                details={
                    "lineage_id": str(review.id),
                    "expected_version": review.version - 1,
                    "stored_version": stored,
                },
            )
        await self._append(review)

    async def _append(self, review: ReportReview) -> None:
        row = review_mark_to_row(review.id, review.last_mark)
        self._session.add(row)
        await self._session.flush()
        self._session.expunge(row)


class SqlAlchemyGuestSubmissionRepository:
    """PostgreSQL-backed implementation of ``GuestSubmissionRepository``.

    Implements: Repository (port ``GuestSubmissionRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, submission_id: EntityId) -> GuestSubmission | None:
        """Return the submission with ``submission_id``.

        Args:
            submission_id: The submission's id.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(GuestSubmissionRow).where(GuestSubmissionRow.id == submission_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_guest_submission(row)

    async def add(self, submission: GuestSubmission) -> None:
        """Insert a new submission.

        Args:
            submission: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        row = GuestSubmissionRow(
            id=submission.id, **guest_submission_to_values(submission)
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"guest submission {submission.id} exists"
            raise ConflictError(
                message, details={"submission_id": str(submission.id)}
            ) from error
        self._session.expunge(row)

    async def save(self, submission: GuestSubmission) -> None:
        """Update a stored submission, checking optimistic concurrency.

        Args:
            submission: The new state; its ``version`` is one more than stored.

        Raises:
            NotFoundError: If no submission with that id exists.
            ConflictError: If it was changed concurrently, or the reference or
                report is already another submission's.
        """
        statement = (
            update(GuestSubmissionRow)
            .where(
                GuestSubmissionRow.id == submission.id,
                GuestSubmissionRow.version == submission.version - 1,
            )
            .values(guest_submission_to_values(submission))
            .returning(GuestSubmissionRow.id)
            .execution_options(synchronize_session=False)
        )
        try:
            async with self._session.begin_nested():
                updated = (await self._session.execute(statement)).scalar_one_or_none()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = "the guest reference or report is already used"
            raise ConflictError(
                message, details={"submission_id": str(submission.id)}
            ) from error
        if updated is not None:
            return
        stored = await self._session.scalar(
            select(GuestSubmissionRow.version).where(
                GuestSubmissionRow.id == submission.id
            )
        )
        if stored is None:
            message = f"guest submission {submission.id} does not exist"
            raise NotFoundError(message, details={"submission_id": str(submission.id)})
        message = f"guest submission {submission.id} was changed concurrently"
        raise ConflictError(
            message,
            details={
                "submission_id": str(submission.id),
                "expected_version": submission.version - 1,
                "stored_version": stored,
            },
        )

    async def lock_cap(self, cap: GuestCap) -> None:
        """Take the cap's advisory lock until the transaction ends.

        Args:
            cap: The cap about to be checked.
        """
        await self._session.execute(
            select(func.pg_advisory_xact_lock(CAP_LOCK_KEYS[cap]))
        )

    async def count_opened_since(self, since: datetime) -> GuestSubmissionWindow:
        """Count the submissions opened at or after ``since``.

        Args:
            since: Start of the window, UTC.

        Returns:
            The count and the oldest opening time in the window.
        """
        count, oldest = (
            await self._session.execute(
                select(
                    func.count(GuestSubmissionRow.id),
                    func.min(GuestSubmissionRow.created_at),
                ).where(GuestSubmissionRow.created_at >= since)
            )
        ).one()
        return GuestSubmissionWindow(count=count, oldest_at=oldest)

    async def count_submitted_since(self, since: datetime) -> GuestSubmissionWindow:
        """Count the reports submitted (reserved) at or after ``since``.

        Args:
            since: Start of the window, UTC.

        Returns:
            The count and the oldest submission time in the window.
        """
        count, oldest = (
            await self._session.execute(
                select(
                    func.count(GuestSubmissionRow.id),
                    func.min(GuestSubmissionRow.submitted_at),
                ).where(GuestSubmissionRow.submitted_at >= since)
            )
        ).one()
        return GuestSubmissionWindow(count=count, oldest_at=oldest)

    async def purge_unfiled(self, expired_before: datetime) -> int:
        """Delete submissions without a filed report that expired before an instant.

        Args:
            expired_before: The retention cut-off.

        Returns:
            How many rows were deleted.
        """
        result = await self._session.execute(
            delete(GuestSubmissionRow)
            .where(
                GuestSubmissionRow.report_id.is_(None),
                GuestSubmissionRow.expires_at < expired_before,
            )
            .returning(GuestSubmissionRow.id)
        )
        return len(result.all())

    async def is_reference_taken(self, reference: str) -> bool:
        """Tell whether a stored submission carries ``reference``.

        Args:
            reference: A candidate reference.

        Returns:
            ``True`` if it is taken.
        """
        found = await self._session.scalar(
            select(GuestSubmissionRow.id).where(
                GuestSubmissionRow.reference == reference
            )
        )
        return found is not None


class SqlAlchemySpentChallengeRepository:
    """PostgreSQL-backed implementation of ``SpentChallengeRepository``.

    Implements: Repository (port ``SpentChallengeRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def spend(self, salt: str, expires_at: datetime) -> None:
        """Insert the redeemed challenge unless it expired by the database's clock.

        The primary key refuses a replay, also when two redemptions race.

        Args:
            salt: The challenge's salt.
            expires_at: When the challenge expires.

        Raises:
            GuestChallengeSpentError: If it was redeemed before.
            GuestChallengeExpiredError: If ``expires_at`` is not after the
                database's ``now()``.
        """
        try:
            async with self._session.begin_nested():
                inserted = (
                    await self._session.execute(
                        _SPEND_CHALLENGE,
                        {"salt": salt, "expires_at": expires_at},
                    )
                ).scalar_one_or_none()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            raise GuestChallengeSpentError.create() from error
        if inserted is None:
            raise GuestChallengeExpiredError.create()

    async def purge_expired(self, grace: timedelta) -> int:
        """Delete redeemed challenges that expired more than ``grace`` ago.

        Args:
            grace: How long after its expiry a record is kept.

        Returns:
            How many rows were deleted.
        """
        result = await self._session.execute(
            _PURGE_CHALLENGES, {"grace_seconds": grace.total_seconds()}
        )
        return len(result.all())
