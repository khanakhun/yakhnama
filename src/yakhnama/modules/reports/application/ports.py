"""Ports the reports application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1). Three of them are answered by other modules, through
their facades, in the composition root, so this module imports none of them:

- ``PhotoEvidenceProvider`` and ``MediaOwnershipChecker`` by the ``media`` module
  (EXIF facts and owners of media assets);
- ``NearbyReportsFinder`` by this module's own read side (a spatial query).

Sources are registered and cited through ``provenance.public.SourceRegistrar`` and
``SourceReferenceMarker``; triage is scheduled through the kernel's ``TaskQueue``.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from collections.abc import Sequence
from typing import Final, Protocol

from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.triage import (
    PhotoEvidence,
    ReportSummaryForTriage,
)
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


class ReportsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the reports repository.

    Implements: Unit of Work.
    """

    @property
    def reports(self) -> ReportRepository:
        """Return the report repository bound to this transaction."""
        ...


type ReportsUnitOfWorkFactory = UnitOfWorkFactory[ReportsUnitOfWork]
"""Opens a fresh reports unit of work per use case."""


class ReportQueryService(Protocol):
    """Read port for reports; returns internal records with exact positions.

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
