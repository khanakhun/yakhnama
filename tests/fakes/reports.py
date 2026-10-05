"""In-memory fakes of the reports ports.

The repository stages writes until the unit of work commits and enforces the
uniqueness and optimistic-concurrency rules of the SQL adapter. The query service
serves committed rows as ``ReportRecord`` through the same specifications the SQL
adapter compiles. The finder, photo provider and ownership checker answer from
what the test arranges and record every call. The guest submission and spent
challenge repositories follow their SQL adapters' rules (optimistic concurrency,
unique references, one row per redeemed challenge); ``SequentialGuestSecretGenerator``
draws predictable salts, capabilities and references, and ``FakeGuestMediaGateway``
records the uploads it grants and completes.

Tests use the canonical ``tests.fakes.tasks.RecordingTaskQueue`` for the
``TaskQueue`` port.

Patterns: Fake.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.media.public import HttpHeader, UploadGrant, UploadStatus
from yakhnama.modules.reports.application.dto import (
    GuestMediaAsset,
    GuestSubmissionWindow,
    ReportRecord,
)
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import GuestChallengeSpentError
from yakhnama.modules.reports.domain.guest_submissions import (
    REFERENCE_ALPHABET,
    GuestSubmission,
)
from yakhnama.modules.reports.domain.triage import (
    PhotoEvidence,
    ReportSummaryForTriage,
)
from yakhnama.modules.reports.domain.value_objects import GuestImageType
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


class InMemoryGuestSubmissionRepository:
    """``GuestSubmissionRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored submissions, as a committed transaction left them.
    """

    def __init__(self, submissions: Iterable[GuestSubmission] = ()) -> None:
        """Create the repository.

        Args:
            submissions: Submissions that exist before the test acts.
        """
        self.committed: dict[EntityId, GuestSubmission] = {
            submission.id: submission for submission in submissions
        }
        self._staged: dict[EntityId, GuestSubmission] = {}

    def _current(self) -> dict[EntityId, GuestSubmission]:
        return {**self.committed, **self._staged}

    async def get(self, submission_id: EntityId) -> GuestSubmission | None:
        """Return the submission, staged changes included.

        Args:
            submission_id: The id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(submission_id)

    async def add(self, submission: GuestSubmission) -> None:
        """Stage a new submission.

        Args:
            submission: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        if submission.id in self._current():
            message = "the guest submission already exists"
            raise ConflictError(message)
        self._staged[submission.id] = submission

    async def save(self, submission: GuestSubmission) -> None:
        """Stage a changed submission.

        Args:
            submission: The new state.

        Raises:
            NotFoundError: If it is not stored.
            ConflictError: If the version does not follow, or the reference is
                another submission's.
        """
        stored = self._current().get(submission.id)
        if stored is None:
            message = f"guest submission {submission.id} is not stored"
            raise NotFoundError(message)
        if submission.version != stored.version + 1:
            message = f"guest submission {submission.id} was changed concurrently"
            raise ConflictError(message)
        if submission.reference is not None and any(
            other.reference == submission.reference and other.id != submission.id
            for other in self._current().values()
        ):
            message = "the guest reference is already used"
            raise ConflictError(message)
        self._staged[submission.id] = submission

    async def count_opened_since(self, since: datetime) -> GuestSubmissionWindow:
        """Count submissions opened at or after ``since``.

        Args:
            since: Start of the window.

        Returns:
            The count and the oldest opening time.
        """
        opened = [
            submission.created_at
            for submission in self._current().values()
            if submission.created_at >= since
        ]
        return GuestSubmissionWindow(
            count=len(opened), oldest_opened_at=min(opened, default=None)
        )

    async def is_reference_taken(self, reference: str) -> bool:
        """Tell whether a submission carries ``reference``.

        Args:
            reference: The candidate.

        Returns:
            ``True`` if taken.
        """
        return any(
            submission.reference == reference for submission in self._current().values()
        )

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemorySpentChallengeRepository:
    """``SpentChallengeRepository`` over a dictionary keyed by salt.

    Implements: Fake (of Repository).

    Attributes:
        committed: Redeemed salts and their expiry, as committed.
    """

    def __init__(self) -> None:
        """Create an empty repository."""
        self.committed: dict[str, datetime] = {}
        self._staged: dict[str, datetime] = {}
        self._purged: set[str] = set()

    async def spend(self, salt: str, expires_at: datetime) -> None:
        """Stage a redeemed challenge.

        Args:
            salt: The salt.
            expires_at: Its expiry.

        Raises:
            GuestChallengeSpentError: If it was redeemed before.
        """
        if salt in self._staged or (
            salt in self.committed and salt not in self._purged
        ):
            raise GuestChallengeSpentError.create()
        self._staged[salt] = expires_at

    async def purge_expired(self, now: datetime) -> int:
        """Stage forgetting the expired challenges.

        Args:
            now: The current instant.

        Returns:
            How many are forgotten.
        """
        expired = {salt for salt, expiry in self.committed.items() if expiry < now}
        self._purged |= expired
        return len(expired)

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        for salt in self._purged:
            self.committed.pop(salt, None)
        self.committed.update(self._staged)
        self._staged.clear()
        self._purged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()
        self._purged.clear()


class InMemoryReportsUnitOfWork(InMemoryUnitOfWork):
    """``ReportsUnitOfWork`` over in-memory repositories.

    Implements: Fake (of Unit of Work).

    Attributes:
        reports: The report repository bound to this unit of work.
        guest_submissions: The guest submission repository.
        spent_challenges: The spent challenge repository.
    """

    def __init__(
        self,
        *,
        reports: Iterable[Report] = (),
        guest_submissions: Iterable[GuestSubmission] = (),
    ) -> None:
        """Create the unit of work.

        Args:
            reports: Reports that exist before the test acts.
            guest_submissions: Guest submissions that exist before the test acts.
        """
        super().__init__()
        self.reports = InMemoryReportRepository(reports)
        self.guest_submissions = InMemoryGuestSubmissionRepository(guest_submissions)
        self.spent_challenges = InMemorySpentChallengeRepository()

    def _on_commit(self) -> None:
        self.reports.apply_staged()
        self.guest_submissions.apply_staged()
        self.spent_challenges.apply_staged()

    def _on_rollback(self) -> None:
        self.reports.discard_staged()
        self.guest_submissions.discard_staged()
        self.spent_challenges.discard_staged()


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


class SequentialGuestSecretGenerator:
    """``GuestSecretGenerator`` drawing predictable values from a counter.

    Implements: Fake (of Adapter).

    Attributes:
        references: References to return first, in order (for collision tests).
    """

    def __init__(self, references: Iterable[str] = ()) -> None:
        """Create the generator.

        Args:
            references: References to return before counting.
        """
        self.references = list(references)
        self._counter = 0

    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def new_salt(self) -> str:
        """Return ``salt-<n>`` padded to 22 characters.

        Returns:
            The salt.
        """
        return f"salt-{self._next()}".ljust(22, "x")

    def new_capability(self) -> str:
        """Return ``capability-<n>`` padded to 43 characters.

        Returns:
            The capability.
        """
        return f"capability-{self._next()}".ljust(43, "x")

    def new_reference(self) -> str:
        """Return the next arranged reference, else one built from the counter.

        Returns:
            The reference.
        """
        if self.references:
            return self.references.pop(0)
        number = self._next()
        letters = "".join(
            REFERENCE_ALPHABET[(number // 30**power) % 30] for power in range(4)
        )
        return f"YK-{letters}-AAAA"


FAKE_GUEST_UPLOAD_EXPIRY = datetime(2026, 6, 1, 13, 0, tzinfo=UTC)


class FakeGuestMediaGateway:
    """``GuestMediaGateway`` that grants and completes uploads in memory.

    Implements: Fake (of Adapter).

    Attributes:
        owners: The owning submission of every granted asset.
        types: The declared image type of every granted asset.
        completed: Assets completed so far, in order.
    """

    def __init__(self, ids: Iterable[EntityId]) -> None:
        """Create the gateway.

        Args:
            ids: The asset ids to hand out, in order.
        """
        self._ids = iter(ids)
        self.owners: dict[EntityId, EntityId] = {}
        self.types: dict[EntityId, GuestImageType] = {}
        self.completed: list[EntityId] = []

    async def request_upload(
        self, owner_id: EntityId, mime_type: GuestImageType
    ) -> UploadGrant:
        """Grant an upload for the next asset id.

        Args:
            owner_id: The submission.
            mime_type: The image type.

        Returns:
            A grant with a fake URL.
        """
        asset_id = next(self._ids)
        self.owners[asset_id] = owner_id
        self.types[asset_id] = mime_type
        return UploadGrant(
            asset_id=asset_id,
            upload_url=f"https://storage.test/upload/{asset_id}",
            headers=(HttpHeader(name="Content-Type", value=mime_type),),
            expires_at=FAKE_GUEST_UPLOAD_EXPIRY,
            max_bytes=1024,
        )

    async def complete_upload(
        self, owner_id: EntityId, asset_id: EntityId
    ) -> GuestMediaAsset:
        """Complete a granted asset.

        Args:
            owner_id: The submission.
            asset_id: The asset.

        Returns:
            The completed photo.

        Raises:
            KeyError: If the asset was not granted to ``owner_id``.
        """
        if self.owners.get(asset_id) != owner_id:
            raise KeyError(asset_id)
        self.completed.append(asset_id)
        return GuestMediaAsset(
            id=asset_id,
            mime_type=self.types[asset_id],
            byte_size=512,
            upload_status=UploadStatus.COMPLETED,
        )
