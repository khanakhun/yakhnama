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
from datetime import UTC, datetime, timedelta

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.media.public import HttpHeader, UploadGrant, UploadStatus
from yakhnama.modules.reports.application.dto import (
    GuestMediaAsset,
    GuestSubmissionWindow,
    LinkedEvent,
    ReportRecord,
    ReportReviewRecord,
)
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import (
    GuestChallengeExpiredError,
    GuestChallengeSpentError,
)
from yakhnama.modules.reports.domain.guest_submissions import (
    REFERENCE_ALPHABET,
    GuestCap,
    GuestSubmission,
)
from yakhnama.modules.reports.domain.reviews import ReportReview, ReviewMark
from yakhnama.modules.reports.domain.triage import (
    PhotoEvidence,
    ReportSummaryForTriage,
)
from yakhnama.modules.reports.domain.value_objects import GuestImageType
from yakhnama.shared_kernel.clock import Clock
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

    async def lineage_of(self, report_id: EntityId) -> EntityId | None:
        """Return the id of the stored report's revision 1, walking the chain.

        Args:
            report_id: Any revision.

        Returns:
            The lineage id, or ``None`` if the report is not stored.
        """
        return lineage_in(self._current(), report_id)

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


def lineage_in(
    reports: Mapping[EntityId, Report], report_id: EntityId
) -> EntityId | None:
    """Return the id of a report's revision 1 among ``reports``.

    Args:
        reports: The stored reports by id.
        report_id: Any revision.

    Returns:
        The lineage id, or ``None`` if the report is not among them.
    """
    report = reports.get(report_id)
    while report is not None and report.supersedes_id is not None:
        report = reports.get(report.supersedes_id)
    return None if report is None else report.id


class InMemoryReportReviewRepository:
    """``ReportReviewRepository`` over a dictionary keyed by lineage id.

    Like the SQL adapter, ``add`` and ``save`` append the review's last mark to the
    lineage's history, which is never changed afterwards.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored reviews, as a committed transaction left them.
        marks: Every committed mark per lineage, oldest first.
    """

    def __init__(self) -> None:
        """Create an empty repository."""
        self.committed: dict[EntityId, ReportReview] = {}
        self.marks: dict[EntityId, list[ReviewMark]] = {}
        self._staged: dict[EntityId, ReportReview] = {}
        self._staged_marks: list[tuple[EntityId, ReviewMark]] = []

    def _current(self) -> dict[EntityId, ReportReview]:
        return {**self.committed, **self._staged}

    async def get(self, lineage_id: EntityId) -> ReportReview | None:
        """Return the review, staged changes included.

        Args:
            lineage_id: The lineage.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(lineage_id)

    async def add(self, review: ReportReview) -> None:
        """Stage a first review and its first mark.

        Args:
            review: The new aggregate.

        Raises:
            ConflictError: If the lineage has a review.
        """
        if review.id in self._current():
            message = "the lineage was marked concurrently"
            raise ConflictError(message)
        self._staged[review.id] = review
        self._staged_marks.append((review.id, review.last_mark))

    async def save(self, review: ReportReview) -> None:
        """Stage a changed review and its new mark.

        Args:
            review: The new state.

        Raises:
            NotFoundError: If the lineage has no review.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(review.id)
        if stored is None:
            message = f"lineage {review.id} has no review"
            raise NotFoundError(message)
        if review.version != stored.version + 1:
            message = f"lineage {review.id} was marked concurrently"
            raise ConflictError(message)
        self._staged[review.id] = review
        self._staged_marks.append((review.id, review.last_mark))

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        for lineage_id, mark in self._staged_marks:
            self.marks.setdefault(lineage_id, []).append(mark)
        self._staged.clear()
        self._staged_marks.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()
        self._staged_marks.clear()


class InMemoryGuestSubmissionRepository:
    """``GuestSubmissionRepository`` over a dictionary keyed by id.

    One process and one shared unit of work serialise everything already, so
    ``lock_cap`` only records which caps were locked, for the tests to check that
    a handler locks before it counts.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored submissions, as a committed transaction left them.
        locked_caps: Every cap locked, in order.
    """

    def __init__(self, submissions: Iterable[GuestSubmission] = ()) -> None:
        """Create the repository.

        Args:
            submissions: Submissions that exist before the test acts.
        """
        self.committed: dict[EntityId, GuestSubmission] = {
            submission.id: submission for submission in submissions
        }
        self.locked_caps: list[GuestCap] = []
        self._staged: dict[EntityId, GuestSubmission] = {}
        self._deleted: set[EntityId] = set()

    def _current(self) -> dict[EntityId, GuestSubmission]:
        current = {**self.committed, **self._staged}
        for submission_id in self._deleted:
            current.pop(submission_id, None)
        return current

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

    async def lock_cap(self, cap: GuestCap) -> None:
        """Record the lock.

        Args:
            cap: The cap.
        """
        self.locked_caps.append(cap)

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
            count=len(opened), oldest_at=min(opened, default=None)
        )

    async def count_submitted_since(self, since: datetime) -> GuestSubmissionWindow:
        """Count reports submitted at or after ``since``.

        Args:
            since: Start of the window.

        Returns:
            The count and the oldest submission time.
        """
        submitted = [
            submission.submitted_at
            for submission in self._current().values()
            if submission.submitted_at is not None and submission.submitted_at >= since
        ]
        return GuestSubmissionWindow(
            count=len(submitted), oldest_at=min(submitted, default=None)
        )

    async def purge_unfiled(self, expired_before: datetime) -> int:
        """Stage deleting unfiled submissions that expired before the cut-off.

        Args:
            expired_before: The cut-off.

        Returns:
            How many are deleted.
        """
        doomed = {
            submission.id
            for submission in self._current().values()
            if submission.report_id is None and submission.expires_at < expired_before
        }
        self._deleted |= doomed
        return len(doomed)

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
        for submission_id in self._deleted:
            self.committed.pop(submission_id, None)
        self._staged.clear()
        self._deleted.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()
        self._deleted.clear()


class InMemorySpentChallengeRepository:
    """``SpentChallengeRepository`` over a dictionary keyed by salt.

    ``database_clock`` plays the database's ``now()``: when set, ``spend`` refuses
    a challenge it says has expired and ``purge_expired`` measures the grace from
    it. Unset, nothing expires.

    Implements: Fake (of Repository).

    Attributes:
        committed: Redeemed salts and their expiry, as committed.
        database_clock: The store's own clock, or ``None``.
    """

    def __init__(self, database_clock: Clock | None = None) -> None:
        """Create an empty repository.

        Args:
            database_clock: The store's own clock, or ``None``.
        """
        self.committed: dict[str, datetime] = {}
        self.database_clock = database_clock
        self._staged: dict[str, datetime] = {}
        self._purged: set[str] = set()

    async def spend(self, salt: str, expires_at: datetime) -> None:
        """Stage a redeemed challenge.

        Args:
            salt: The salt.
            expires_at: Its expiry.

        Raises:
            GuestChallengeSpentError: If it was redeemed before.
            GuestChallengeExpiredError: If ``database_clock`` says it expired.
        """
        if salt in self._staged or (
            salt in self.committed and salt not in self._purged
        ):
            raise GuestChallengeSpentError.create()
        if self.database_clock is not None and expires_at <= self.database_clock.now():
            raise GuestChallengeExpiredError.create()
        self._staged[salt] = expires_at

    async def purge_expired(self, grace: timedelta) -> int:
        """Stage forgetting the challenges expired more than ``grace`` ago.

        Args:
            grace: How long after expiry a record is kept.

        Returns:
            How many are forgotten.
        """
        if self.database_clock is None:
            return 0
        cutoff = self.database_clock.now() - grace
        expired = {salt for salt, expiry in self.committed.items() if expiry < cutoff}
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
        report_reviews: The review repository.
    """

    def __init__(
        self,
        *,
        reports: Iterable[Report] = (),
        guest_submissions: Iterable[GuestSubmission] = (),
        database_clock: Clock | None = None,
    ) -> None:
        """Create the unit of work.

        Args:
            reports: Reports that exist before the test acts.
            guest_submissions: Guest submissions that exist before the test acts.
            database_clock: The spent challenge store's own clock, or ``None``.
        """
        super().__init__()
        self.reports = InMemoryReportRepository(reports)
        self.guest_submissions = InMemoryGuestSubmissionRepository(guest_submissions)
        self.spent_challenges = InMemorySpentChallengeRepository(database_clock)
        self.report_reviews = InMemoryReportReviewRepository()

    def _on_commit(self) -> None:
        self.reports.apply_staged()
        self.guest_submissions.apply_staged()
        self.spent_challenges.apply_staged()
        self.report_reviews.apply_staged()

    def _on_rollback(self) -> None:
        self.reports.discard_staged()
        self.guest_submissions.discard_staged()
        self.spent_challenges.discard_staged()
        self.report_reviews.discard_staged()


def _newest_first(record: ReportRecord) -> tuple[datetime, EntityId]:
    return record.created_at, record.id


class InMemoryReportQueryService:
    """``ReportQueryService`` reading a fake unit of work's committed rows.

    Records carry their lineage and their lineage's committed review, as the SQL
    adapter's joins do. Event links come from what the test arranges in
    ``linked_events`` (the events module's projection is not here).

    Implements: Fake (of Query Service).

    Attributes:
        linked_events: Arranged links, by the linked report revision's id.
    """

    def __init__(self, uow: InMemoryReportsUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow
        self.linked_events: dict[EntityId, tuple[LinkedEvent, ...]] = {}

    def _record(self, report: Report) -> ReportRecord:
        committed = self._uow.reports.committed
        lineage_id = lineage_in(committed, report.id)
        review = (
            None
            if lineage_id is None
            else self._uow.report_reviews.committed.get(lineage_id)
        )
        review_record = None
        if review is not None:
            review_record = ReportReviewRecord(
                state=review.state,
                reviewed_report_id=review.last_mark.report_id,
                reviewed_revision=review.last_mark.revision,
                updated_at=review.updated_at,
                updated_by=review.updated_by,
                version=review.version,
                latest_revision=max(
                    other.revision
                    for other in committed.values()
                    if lineage_in(committed, other.id) == lineage_id
                ),
            )
        return ReportRecord.from_entity(report).model_copy(
            update={"lineage_id": lineage_id, "review": review_record}
        )

    async def get_report(self, report_id: EntityId) -> ReportRecord | None:
        """Return one committed report.

        Args:
            report_id: The report.

        Returns:
            The record, or ``None``.
        """
        report = self._uow.reports.committed.get(report_id)
        return None if report is None else self._record(report)

    async def list_review_marks(
        self, lineage_id: EntityId, limit: int
    ) -> tuple[ReviewMark, ...]:
        """Return the lineage's committed marks, newest first.

        Args:
            lineage_id: The lineage.
            limit: Most marks returned.

        Returns:
            The marks.
        """
        marks = self._uow.report_reviews.marks.get(lineage_id, [])
        newest_first = sorted(
            marks, key=lambda mark: (mark.marked_at, mark.id), reverse=True
        )
        return tuple(newest_first[:limit])

    async def list_linked_events(self, lineage_id: EntityId) -> tuple[LinkedEvent, ...]:
        """Return the arranged links of every committed revision of the lineage.

        Args:
            lineage_id: The lineage.

        Returns:
            The links, in arrangement order of the revisions.
        """
        committed = self._uow.reports.committed
        return tuple(
            link
            for report_id, links in self.linked_events.items()
            if lineage_in(committed, report_id) == lineage_id
            for link in links
        )

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
                for record in map(self._record, self._uow.reports.committed.values())
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
        sizes: The signed size of every granted asset.
        completed: Assets completed so far, in order.
    """

    def __init__(self) -> None:
        """Create the gateway."""
        self.owners: dict[EntityId, EntityId] = {}
        self.types: dict[EntityId, GuestImageType] = {}
        self.sizes: dict[EntityId, int] = {}
        self.completed: list[EntityId] = []

    async def request_upload(
        self,
        owner_id: EntityId,
        mime_type: GuestImageType,
        *,
        asset_id: EntityId,
        byte_size: int,
    ) -> UploadGrant:
        """Grant an upload for the reserved asset id.

        Args:
            owner_id: The submission.
            mime_type: The image type.
            asset_id: The reserved asset id.
            byte_size: The signed size.

        Returns:
            A grant with a fake URL.

        Raises:
            ConflictError: If the asset id was granted before.
        """
        if asset_id in self.owners:
            message = "the media asset already exists"
            raise ConflictError(message)
        self.owners[asset_id] = owner_id
        self.types[asset_id] = mime_type
        self.sizes[asset_id] = byte_size
        return UploadGrant(
            asset_id=asset_id,
            upload_url=f"https://storage.test/upload/{asset_id}",
            headers=(
                HttpHeader(name="Content-Type", value=mime_type),
                HttpHeader(name="Content-Length", value=str(byte_size)),
            ),
            expires_at=FAKE_GUEST_UPLOAD_EXPIRY,
            max_bytes=50 * 1024 * 1024,
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
