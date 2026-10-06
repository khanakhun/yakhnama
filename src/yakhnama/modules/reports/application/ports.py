"""Ports the reports application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1). Three of them are answered by other modules, through
their facades, in the composition root, so this module imports none of them:

- ``PhotoEvidenceProvider`` and ``MediaOwnershipChecker`` by the ``media`` module
  (EXIF facts and owners of media assets);
- ``NearbyReportsFinder`` by this module's own read side (a spatial query).

Sources are registered and cited through ``provenance.public.SourceRegistrar`` and
``SourceReferenceMarker``; triage is scheduled through the kernel's ``TaskQueue``.

The guest submission use cases (ADR 0020) add two repositories to the unit of work,
a ``GuestChallengeSigner`` and a ``GuestSecretGenerator`` (crypto and randomness
stay in infrastructure, so tests are deterministic), and the ``GuestMediaGateway``
the media module answers in the composition root. ``UploadGrant`` is the media
module's own DTO, taken from its facade, so a guest upload grant has the same shape
as an account one.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final, Protocol

from yakhnama.modules.media.public import UploadGrant
from yakhnama.modules.reports.application.dto import (
    GuestMediaAsset,
    GuestSubmissionWindow,
    LinkedEvent,
    ReportRecord,
)
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.guest_submissions import (
    GuestCap,
    GuestChallenge,
    GuestSubmission,
)
from yakhnama.modules.reports.domain.reviews import ReportReview, ReviewMark
from yakhnama.modules.reports.domain.triage import (
    PhotoEvidence,
    ReportSummaryForTriage,
)
from yakhnama.modules.reports.domain.value_objects import GuestImageType
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

RUN_TRIAGE_TASK: Final = "reports.run_triage"
"""Task name under which ``RunTriageHandler`` is enqueued; payload ``{report_id}``."""


class ReportRepository(Protocol):
    """Loads and stages ``Report`` aggregates inside one unit of work.

    There is no delete: reports are never removed (``AGENTS.md`` §5).

    Implements: Repository (port side).
    """

    async def get(self, report_id: EntityId) -> Report | None:
        """Return the report with ``report_id``, whatever its status.

        Args:
            report_id: The report's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, report: Report) -> None:
        """Stage a new report or a new revision.

        Args:
            report: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken, including by a concurrent
                submission with the same client id.
        """
        ...

    async def save(self, report: Report) -> None:
        """Stage a changed report, checking optimistic concurrency.

        Args:
            report: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If no report with that id exists.
            ConflictError: If the stored version is not ``report.version - 1``.
        """
        ...

    async def lineage_of(self, report_id: EntityId) -> EntityId | None:
        """Return the lineage of a stored report: the id of its revision 1.

        Args:
            report_id: Any revision.

        Returns:
            The lineage id, or ``None`` if the report is not stored.
        """
        ...


class ReportReviewRepository(Protocol):
    """Loads and stages ``ReportReview`` aggregates; marks are only ever appended.

    ``add`` and ``save`` also append the review's ``last_mark`` to the lineage's
    history, in the same transaction; nothing removes or changes a stored mark.

    Implements: Repository (port side).
    """

    async def get(self, lineage_id: EntityId) -> ReportReview | None:
        """Return the review of a lineage.

        Args:
            lineage_id: The id of the lineage's revision 1.

        Returns:
            The aggregate, or ``None`` if the lineage was never marked.
        """
        ...

    async def add(self, review: ReportReview) -> None:
        """Stage a lineage's first review and its first mark.

        Args:
            review: The new aggregate at version 1.

        Raises:
            ConflictError: If the lineage already has a review (a concurrent
                first mark).
        """
        ...

    async def save(self, review: ReportReview) -> None:
        """Stage a changed review and append its new last mark.

        Args:
            review: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If the lineage has no stored review.
            ConflictError: If the stored version is not ``review.version - 1``.
        """
        ...


class GuestSubmissionRepository(Protocol):
    """Loads and stages ``GuestSubmission`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, submission_id: EntityId) -> GuestSubmission | None:
        """Return the submission with ``submission_id``.

        Args:
            submission_id: The submission's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, submission: GuestSubmission) -> None:
        """Stage a new submission.

        Args:
            submission: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        ...

    async def save(self, submission: GuestSubmission) -> None:
        """Stage a changed submission, checking optimistic concurrency.

        Args:
            submission: The new state; its ``version`` is one more than stored.

        Raises:
            NotFoundError: If no submission with that id exists.
            ConflictError: If the stored version is not ``version - 1``, or the
                reference is already used by another submission.
        """
        ...

    async def lock_cap(self, cap: GuestCap) -> None:
        """Serialise the checks of ``cap`` until this unit of work ends.

        A transaction that counts towards a cap and then writes must hold the
        cap's lock first, so a concurrent transaction counts only after the first
        one committed or rolled back and the cap is never overshot.

        Args:
            cap: The cap about to be checked.
        """
        ...

    async def count_opened_since(self, since: datetime) -> GuestSubmissionWindow:
        """Count the submissions opened at or after ``since``, by all guests.

        Args:
            since: Start of the window, UTC.

        Returns:
            The count and when the oldest of them was opened.
        """
        ...

    async def count_submitted_since(self, since: datetime) -> GuestSubmissionWindow:
        """Count the guest reports submitted (reserved) at or after ``since``.

        Args:
            since: Start of the window, UTC.

        Returns:
            The count and when the oldest of them was submitted.
        """
        ...

    async def purge_unfiled(self, expired_before: datetime) -> int:
        """Delete submissions that never filed a report and expired before an instant.

        Filed submissions are kept: they carry the receipt reference a guest may
        quote, and the report names them as its reporter.

        Args:
            expired_before: Only submissions whose capability expired before this
                instant are deleted.

        Returns:
            How many were deleted.
        """
        ...

    async def is_reference_taken(self, reference: str) -> bool:
        """Tell whether a receipt reference was already handed out.

        Args:
            reference: A candidate reference.

        Returns:
            ``True`` if a stored submission carries it.
        """
        ...


class SpentChallengeRepository(Protocol):
    """Remembers redeemed challenges until they expire, so each opens one submission.

    Implements: Repository (port side).
    """

    async def spend(self, salt: str, expires_at: datetime) -> None:
        """Record that the challenge with ``salt`` was redeemed.

        The store's own clock decides whether the challenge has expired, the same
        clock ``purge_expired`` uses, so a challenge can never be spendable again
        after its record was purged, whatever the application servers' clocks
        say.

        Args:
            salt: The challenge's salt, unique per challenge.
            expires_at: When the challenge expires; it may be forgotten after.

        Raises:
            GuestChallengeSpentError: If it was redeemed before.
            GuestChallengeExpiredError: If it has expired by the store's clock.
        """
        ...

    async def purge_expired(self, grace: timedelta) -> int:
        """Forget redeemed challenges that expired more than ``grace`` ago.

        Uses the store's own clock, like ``spend``; the grace margin covers the
        transactions still running when a challenge expires.

        Args:
            grace: How long after its expiry a record is kept.

        Returns:
            How many were forgotten.
        """
        ...


class ReportsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the reports and guest submission repositories.

    A guest report and the submission that carried it are written in one
    transaction, so a submission is never closed without its report.

    Implements: Unit of Work.
    """

    @property
    def reports(self) -> ReportRepository:
        """Return the report repository bound to this transaction."""
        ...

    @property
    def guest_submissions(self) -> GuestSubmissionRepository:
        """Return the guest submission repository bound to this transaction."""
        ...

    @property
    def report_reviews(self) -> ReportReviewRepository:
        """Return the review repository bound to this transaction."""
        ...

    @property
    def spent_challenges(self) -> SpentChallengeRepository:
        """Return the spent challenge repository bound to this transaction."""
        ...


type ReportsUnitOfWorkFactory = UnitOfWorkFactory[ReportsUnitOfWork]
"""Opens a fresh reports unit of work per use case."""


class ReportQueryService(Protocol):
    """Read port for reports; returns internal records with exact positions.

    Every record it returns carries its ``lineage_id`` and its lineage's stored
    ``review`` (``None`` when never marked), so the application can show the
    mark to moderators and filter on it.

    Authorisation and the privacy rules are applied by
    ``AuthorisedReportQueryService``; implementations only read.

    Implements: Query Service.
    """

    async def get_report(self, report_id: EntityId) -> ReportRecord | None:
        """Return one report, whatever its status.

        Args:
            report_id: The report.

        Returns:
            The record, or ``None``.
        """
        ...

    async def list_reports(
        self, specification: Specification[ReportRecord], page: PageRequest
    ) -> Page[ReportRecord]:
        """Return one page of the reports ``specification`` accepts.

        Ordered by ``created_at`` descending (newest first), then by id descending;
        the cursor's ``sort_key`` is ``created_at`` in ISO 8601 and its ``last_id``
        the last report's id.

        Args:
            specification: The filter, compiled to SQL by the adapter (see
                ``specifications`` for the rounded bounding box).
            page: Page size and cursor.

        Returns:
            Up to ``page.limit`` records and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def list_review_marks(
        self, lineage_id: EntityId, limit: int
    ) -> tuple[ReviewMark, ...]:
        """Return a lineage's review marks, newest first.

        Args:
            lineage_id: The id of the lineage's revision 1.
            limit: Most marks returned.

        Returns:
            Up to ``limit`` marks, ordered by ``marked_at`` then id, descending.
        """
        ...

    async def list_linked_events(self, lineage_id: EntityId) -> tuple[LinkedEvent, ...]:
        """Return the event links of every revision of a lineage.

        Read from the events module's projection of its report links; this
        module never writes it.

        Args:
            lineage_id: The id of the lineage's revision 1.

        Returns:
            The links, oldest first.
        """
        ...


class NearbyReportsFinder(Protocol):
    """Finds current reports near a point in space and time, for triage.

    Implements: Query Service.
    """

    async def find_nearby(
        self, query: FindNearbyReports
    ) -> tuple[ReportSummaryForTriage, ...]:
        """Return submitted (current) reports near a point and time.

        The exact positions are used: triage runs inside the platform and its flag
        details never repeat a position. The domain rules filter again, so an
        adapter may return a superset (for example a bounding-box prefilter), but
        never more than ``query.limit`` candidates, nearest first.

        Args:
            query: The centre, time, radius, window, limit and excluded report.

        Returns:
            Up to ``query.limit`` candidates.
        """
        ...


class PhotoEvidenceProvider(Protocol):
    """Returns the EXIF facts of a report's photos, from the ``media`` module.

    Implements: Adapter (port side).
    """

    async def photos_for(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> tuple[PhotoEvidence, ...]:
        """Return the EXIF facts of the listed assets that ``owner_id`` uploaded.

        Assets that do not exist, belong to someone else or have not completed
        their upload are left out.

        Args:
            media_ids: The report's media assets.
            owner_id: The reporter.

        Returns:
            One entry per usable asset, in ``media_ids`` order.
        """
        ...


class MediaOwnershipChecker(Protocol):
    """Tells whether media assets were uploaded by a user, from the ``media`` module.

    Implements: Adapter (port side).
    """

    async def is_owned_by(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> bool:
        """Tell whether every listed asset exists and was uploaded by ``owner_id``.

        Args:
            media_ids: The assets a report wants to attach.
            owner_id: The reporter.

        Returns:
            ``True`` if all of them are ``owner_id``'s; ``True`` for no assets.
        """
        ...


class GuestChallengeSigner(Protocol):
    """Signs proof-of-work challenges and verifies them when they come back.

    The challenge carries its own salt, difficulty and expiry under a keyed
    signature, so the platform stores nothing until a challenge is redeemed.

    Implements: Adapter (port side).
    """

    def sign(self, challenge: GuestChallenge) -> str:
        """Return the opaque, signed form of ``challenge``.

        Args:
            challenge: The challenge to hand out.

        Returns:
            At most 512 URL-safe characters.
        """
        ...

    def verify(self, token: str) -> GuestChallenge | None:
        """Return the challenge ``token`` carries, if the platform signed it.

        Expiry is not checked here; the use case compares it with its clock.

        Args:
            token: What the client sent back.

        Returns:
            The challenge, or ``None`` if the token is malformed or its signature
            is not the platform's.
        """
        ...


class GuestSecretGenerator(Protocol):
    """Draws the random values of guest submissions.

    Implements: Adapter (port side).
    """

    def new_salt(self) -> str:
        """Return a fresh challenge salt (at least 128 random bits, URL-safe).

        Returns:
            The salt.
        """
        ...

    def new_capability(self) -> str:
        """Return a fresh capability (at least 256 random bits, URL-safe).

        Returns:
            The capability.
        """
        ...

    def new_reference(self) -> str:
        """Return a fresh receipt reference such as ``YK-7KQM-3HXA``.

        Returns:
            The reference; uniqueness is checked by the use case.
        """
        ...


class GuestMediaGateway(Protocol):
    """Requests and completes a guest's photo uploads, from the ``media`` module.

    The asset is owned by the guest submission; the caller has checked the
    guest's capability and reserved the photo slot for ``asset_id`` first.

    Implements: Adapter (port side).
    """

    async def request_upload(
        self,
        owner_id: EntityId,
        mime_type: GuestImageType,
        *,
        asset_id: EntityId,
        byte_size: int,
    ) -> UploadGrant:
        """Create the asset ``asset_id`` owned by ``owner_id`` and presign its upload.

        Args:
            owner_id: The guest submission.
            mime_type: The declared image type.
            asset_id: The id the reserved slot names.
            byte_size: The file's exact size; storage refuses any other length.

        Returns:
            The media module's upload grant.
        """
        ...

    async def complete_upload(
        self, owner_id: EntityId, asset_id: EntityId
    ) -> GuestMediaAsset:
        """Check and complete the uploaded photo.

        Args:
            owner_id: The guest submission.
            asset_id: The asset.

        Returns:
            The completed asset, or the submission's earlier identical one.

        Raises:
            ValidationError: If the file is missing, empty, too large or not the
                declared image type.
        """
        ...
