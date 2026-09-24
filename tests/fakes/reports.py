"""In-memory fakes of the reports ports.

The repository stages writes until the unit of work commits and enforces the
uniqueness and optimistic-concurrency rules of the SQL adapter. The query service
serves committed rows as ``ReportRecord`` through the same specifications the SQL
adapter compiles. The finder, photo provider and ownership checker answer from
what the test arranges and record every call.

Tests use the canonical ``tests.fakes.tasks.RecordingTaskQueue`` for the
``TaskQueue`` port.

Patterns: Fake.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.triage import (
    PhotoEvidence,
    ReportSummaryForTriage,
)
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.specification import Specification


class InMemoryReportRepository:
    """``ReportRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored reports, as a committed transaction left them.
    """

    def __init__(self, reports: Iterable[Report] = ()) -> None:
        """Create the repository.

        Args:
            reports: Reports that exist before the test acts.
        """
        self.committed: dict[EntityId, Report] = {
            report.id: report for report in reports
        }
        self._staged: dict[EntityId, Report] = {}

    def _current(self) -> dict[EntityId, Report]:
        return {**self.committed, **self._staged}

    async def get(self, report_id: EntityId) -> Report | None:
        """Return the report, staged changes included.

        Args:
            report_id: The report's id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(report_id)

    async def add(self, report: Report) -> None:
        """Stage a new report.

        Args:
            report: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        if report.id in self._current():
            message = "the report already exists"
            raise ConflictError(message)
        self._staged[report.id] = report

    async def save(self, report: Report) -> None:
        """Stage a changed report.

        Args:
            report: The new state.

        Raises:
            NotFoundError: If the report is not stored.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(report.id)
        if stored is None:
            message = f"report {report.id} is not stored"
            raise NotFoundError(message)
        if report.version != stored.version + 1:
            message = f"report {report.id} was changed concurrently"
            raise ConflictError(message)
        self._staged[report.id] = report

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryReportsUnitOfWork(InMemoryUnitOfWork):
    """``ReportsUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        reports: The report repository bound to this unit of work.
    """

    def __init__(self, *, reports: Iterable[Report] = ()) -> None:
        """Create the unit of work.

        Args:
            reports: Reports that exist before the test acts.
        """
        super().__init__()
        self.reports = InMemoryReportRepository(reports)

    def _on_commit(self) -> None:
        self.reports.apply_staged()

    def _on_rollback(self) -> None:
        self.reports.discard_staged()


def _newest_first(record: ReportRecord) -> tuple[datetime, EntityId]:
    return record.created_at, record.id


class InMemoryReportQueryService:
    """``ReportQueryService`` reading a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryReportsUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def get_report(self, report_id: EntityId) -> ReportRecord | None:
        """Return one committed report.

        Args:
            report_id: The report.

        Returns:
            The record, or ``None``.
        """
        report = self._uow.reports.committed.get(report_id)
        return None if report is None else ReportRecord.from_entity(report)

    async def list_reports(
        self, specification: Specification[ReportRecord], page: PageRequest
    ) -> Page[ReportRecord]:
        """Page the matching reports by ``(created_at, id)`` descending.

        Args:
            specification: The filter.
            page: Page size and cursor.

        Returns:
            One page of records.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = page.decode_cursor()
        records = sorted(
            (
                record
                for record in map(
                    ReportRecord.from_entity, self._uow.reports.committed.values()
                )
                if specification.is_satisfied_by(record)
            ),
            key=_newest_first,
            reverse=True,
        )
        if cursor is not None:
            before = (datetime.fromisoformat(cursor.sort_key), cursor.last_id)
            records = [record for record in records if _newest_first(record) < before]
        window = records[: page.limit]
        next_cursor = None
        if len(records) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[ReportRecord](items=tuple(window), next_cursor=next_cursor)


class FakeNearbyReportsFinder:
    """``NearbyReportsFinder`` returning what the test arranged.

    Implements: Fake (of Query Service).

    Attributes:
        candidates: The candidates every call returns.
        queries: Every query received, in order.
    """

    def __init__(self, candidates: Iterable[ReportSummaryForTriage] = ()) -> None:
        """Create the finder.

        Args:
            candidates: The candidates to return.
        """
        self.candidates = tuple(candidates)
        self.queries: list[FindNearbyReports] = []

    async def find_nearby(
        self, query: FindNearbyReports
    ) -> tuple[ReportSummaryForTriage, ...]:
        """Record ``query`` and return the arranged candidates.

        Args:
            query: The search.

        Returns:
            The arranged candidates, at most ``query.limit``.
        """
        self.queries.append(query)
        return self.candidates[: query.limit]


class FakePhotoEvidenceProvider:
    """``PhotoEvidenceProvider`` answering from arranged EXIF facts per owner.

    Implements: Fake (of Adapter).

    Attributes:
        calls: Every ``(media_ids, owner_id)`` asked, in order.
    """

    def __init__(
        self, evidence: Mapping[EntityId, tuple[EntityId, PhotoEvidence]] | None = None
    ) -> None:
        """Create the provider.

        Args:
            evidence: For each media id, its owner and its EXIF facts.
        """
        self._evidence = dict(evidence or {})
        self.calls: list[tuple[tuple[EntityId, ...], EntityId]] = []

    async def photos_for(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> tuple[PhotoEvidence, ...]:
        """Return the arranged facts of ``owner_id``'s listed assets.

        Args:
            media_ids: The report's media assets.
            owner_id: The reporter.

        Returns:
            The facts of the assets arranged for that owner, in order.
        """
        self.calls.append((tuple(media_ids), owner_id))
        owned = [
            entry for media_id in media_ids if (entry := self._evidence.get(media_id))
        ]
        return tuple(photo for owner, photo in owned if owner == owner_id)


class FakeMediaOwnershipChecker:
    """``MediaOwnershipChecker`` answering from arranged owners.

    Implements: Fake (of Adapter).

    Attributes:
        owners: The owner of each known media asset.
        calls: How many times it was asked.
    """

    def __init__(self, owners: Mapping[EntityId, EntityId] | None = None) -> None:
        """Create the checker.

        Args:
            owners: The owner of each known media asset.
        """
        self.owners = dict(owners or {})
        self.calls = 0

    async def is_owned_by(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> bool:
        """Tell whether every listed asset is known and owned by ``owner_id``.

        Args:
            media_ids: The assets.
            owner_id: The would-be owner.

        Returns:
            ``True`` if all are ``owner_id``'s.
        """
        self.calls += 1
        return all(self.owners.get(media_id) == owner_id for media_id in media_ids)
