"""Write-side use cases of the reports module.

**Submission** (``SubmitReportHandler``) spans three modules that never share a
transaction, so it runs as ordered steps, each safe to repeat:

1. the policy is checked before anything is read (deny by default);
2. if a report with the client's id exists, the call is a retry: the stored
   report is returned and nothing else happens (no source, no event, no task);
3. the attached media are checked to be the reporter's own;
4. a ``citizen`` (or ``organisation``) source is registered through the
   provenance facade;
5. the report is created, submitted and committed with ``ReportSubmitted``;
6. the source is marked referenced through the provenance facade;
7. triage is **enqueued**, not run inline: it reads other reports and the media
   module and must not slow down or fail a submission from a phone on a weak
   connection, and the queue retries it independently.

A failure between steps 4 and 5 leaves an unreferenced source that nothing cites;
between 5 and 7 a report that is stored but not yet triaged or not yet marking its
source. Both are harmless and visible (an open question proposes a sweep that
re-enqueues untriaged reports; the kernel's ``TaskQueue`` notes that the outbox is
the robust trigger).

**Corrections** follow the domain's two-step pattern: ``revise`` returns the new
revision and ``mark_superseded`` the old one, both saved in one unit of work.

Patterns: Command Handler, Unit of Work, Policy, Domain Events, Chain of
Responsibility.
"""

from typing import Final

from yakhnama.modules.provenance.public import (
    MarkSourceReferenced,
    RegisterSource,
    SourceDetails,
    SourceReferenceMarker,
    SourceRegistrar,
    SourceType,
)
from yakhnama.modules.reports.application.authorisation import (
    reporter_policy,
    require_allowed,
    require_user,
    submit_policy,
)
from yakhnama.modules.reports.application.commands import (
    ReviseReport,
    RunTriage,
    SubmitReport,
    WithdrawReport,
)
from yakhnama.modules.reports.application.dto import ReportDetail
from yakhnama.modules.reports.application.ports import (
    RUN_TRIAGE_TASK,
    MediaOwnershipChecker,
    NearbyReportsFinder,
    PhotoEvidenceProvider,
    ReportsUnitOfWork,
    ReportsUnitOfWorkFactory,
)
from yakhnama.modules.reports.application.queries import FindNearbyReports
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import ReportNotFoundError
from yakhnama.modules.reports.domain.factories import ReportFactory
from yakhnama.modules.reports.domain.triage import (
    DUPLICATE_DISTANCE_METRES,
    DUPLICATE_TIME_WINDOW,
    NEARBY_REPORTS_MAX,
    TriageChain,
    TriageContext,
)
from yakhnama.modules.reports.domain.value_objects import (
    ReportAttribution,
    ReportContent,
    TriageResult,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import (
    ConflictError,
    PermissionDeniedError,
    PreconditionFailedError,
)
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.tasks import TaskQueue

# Platform-written titles and citations for the source behind each report
# (**proposed**). They name neither the reporter nor the report: a citation is
# shown wherever the source is, and a report id there would point at a private,
# unverified report (security review, Phase 3). The report links to its source,
# never the other way round.
CITIZEN_SOURCE_TITLE: Final = "Community report"
ORGANISATION_SOURCE_TITLE: Final = "Organisation report"
CITIZEN_SOURCE_CITATION: Final = "Yakhnama community report"
ORGANISATION_SOURCE_CITATION: Final = "Yakhnama organisation report"


def _source_details(*, is_organisation: bool) -> SourceDetails:
    if is_organisation:
        return SourceDetails(
            title=ORGANISATION_SOURCE_TITLE, citation=ORGANISATION_SOURCE_CITATION
        )
    return SourceDetails(title=CITIZEN_SOURCE_TITLE, citation=CITIZEN_SOURCE_CITATION)


async def _load_report(uow: ReportsUnitOfWork, report_id: EntityId) -> Report:
    report = await uow.reports.get(report_id)
    if report is None:
        raise ReportNotFoundError.for_id(report_id)
    return report


def _check_version(expected: int | None, current: int) -> None:
    # Compared inside the unit of work, after the load, so the gap between the
    # client's read and this write is closed.
    if expected is not None and expected != current:
        message = "the record has changed since the client read it"
        raise PreconditionFailedError(
            message,
            details={"expected_version": expected, "current_version": current},
        )


async def _require_own_media(
    checker: MediaOwnershipChecker, content: ReportContent, reporter_id: EntityId
) -> None:
    # Attaching someone else's asset would publish their photo under this report
    # and feed their EXIF position into this report's triage.
    if content.media_ids and not await checker.is_owned_by(
        content.media_ids, reporter_id
    ):
        message = "the actor may attach only media assets they uploaded"
        raise PermissionDeniedError(
            message, details={"action": "attach media", "reason": "not_owner"}
        )


async def _enqueue_triage(task_queue: TaskQueue, report_id: EntityId) -> None:
    # The key names the report, so a repeated enqueue of the same revision is
    # recognisable; a new revision has a new id and is triaged on its own.
    await task_queue.enqueue(
        RUN_TRIAGE_TASK,
        {"report_id": report_id},
        idempotency_key=f"{RUN_TRIAGE_TASK}:{report_id}",
    )


class SubmitReportHandler:
    """Submit a report idempotently by its client id (steps in the module docs).

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: ReportsUnitOfWorkFactory,
        source_registrar: SourceRegistrar,
        source_marker: SourceReferenceMarker,
        media_checker: MediaOwnershipChecker,
        task_queue: TaskQueue,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a reports unit of work per step.
            source_registrar: Registers the report's source (provenance).
            source_marker: Marks that source referenced (provenance).
            media_checker: Confirms the attached media are the reporter's.
            task_queue: Schedules the triage task.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._source_registrar = source_registrar
        self._source_marker = source_marker
        self._media_checker = media_checker
        self._task_queue = task_queue
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: SubmitReport) -> ReportDetail:
        """Submit the report, or return it if this client id was already submitted.

        Args:
            command: The validated command.

        Returns:
            The reporter's exact view of the report.

        Raises:
            PermissionDeniedError: If the actor is anonymous, not a member of the
                named organisation, or attaches media they did not upload.
            ConflictError: If the client id was used before by another reporter,
                with other content or for another organisation, or concurrently.
        """
        reporter_id = require_user(command.actor, action="submit reports")
        require_allowed(
            submit_policy(command.organization_id),
            command.actor,
            action="submit reports",
        )
        async with self._uow_factory() as uow:
            existing = await uow.reports.get(command.client_report_id)
        if existing is not None:
            return self._replay(existing, command)
        await _require_own_media(self._media_checker, command.content, reporter_id)
        source = await self._source_registrar(
            RegisterSource(
                actor=command.actor,
                source_type=(
                    SourceType.CITIZEN
                    if command.organization_id is None
                    else SourceType.ORGANISATION
                ),
                details=_source_details(
                    is_organisation=command.organization_id is not None
                ),
                organization_id=command.organization_id,
            )
        )
        attribution = ReportAttribution(
            reporter_id=reporter_id,
            organization_id=command.organization_id,
            source_id=source.id,
        )
        async with self._uow_factory() as uow:
            report = (
                ReportFactory()
                .submitted(
                    command.client_report_id,
                    attribution,
                    command.content,
                    clock=self._clock,
                    ids=self._ids,
                )
                .record_into(uow)
            )
            await uow.reports.add(report)
            await uow.commit()
        await self._source_marker(
            MarkSourceReferenced(actor=command.actor, source_id=source.id)
        )
        await _enqueue_triage(self._task_queue, report.id)
        return ReportDetail.for_reporter(report)

    @staticmethod
    def _replay(existing: Report, command: SubmitReport) -> ReportDetail:
        # A retry carries the same reporter, organisation and content. Anything else
        # is a different report reusing the id, refused without saying whose it is.
        is_retry = (
            existing.reporter_id == command.actor.user_id
            and existing.organization_id == command.organization_id
            and existing.revision == 1
            and existing.content == command.content
        )
        if not is_retry:
            message = "the client report id is already in use"
            raise ConflictError(message, details={"reason": "client_report_id_taken"})
        return ReportDetail.for_reporter(existing)


class ReviseReportHandler:
    """Correct a report by submitting its next revision; the reporter only.

    Implements: Command Handler.
    """

    def __init__(
        self,
        *,
        uow_factory: ReportsUnitOfWorkFactory,
        media_checker: MediaOwnershipChecker,
        task_queue: TaskQueue,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a reports unit of work per call.
            media_checker: Confirms the attached media are the reporter's.
            task_queue: Schedules triage of the new revision.
            clock: Source of timestamps and event times.
            ids: Source of the new revision's id and event ids.
        """
        self._uow_factory = uow_factory
        self._media_checker = media_checker
        self._task_queue = task_queue
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: ReviseReport) -> ReportDetail:
        """Store the new revision and mark the old one superseded, together.

        Args:
            command: The validated command.

        Returns:
            The reporter's exact view of the new revision.

        Raises:
            ReportNotFoundError: If the report does not exist.
            PermissionDeniedError: If the actor is not the reporter, or attaches
                media they did not upload.
            PreconditionFailedError: If ``expected_version`` is stale.
            ReportImmutableError: If the report was already superseded.
            ReportWithdrawnError: If the report was withdrawn.
            ReportNotSubmittedError: If the report is a draft.
            ReportRevisionUnchangedError: If the content is unchanged.
        """
        async with self._uow_factory() as uow:
            current = await _load_report(uow, command.report_id)
            require_allowed(
                reporter_policy(current.reporter_id),
                command.actor,
                action="revise this report",
            )
            _check_version(command.expected_version, current.version)
            await _require_own_media(
                self._media_checker, command.content, current.reporter_id
            )
            revision = current.revise(
                command.content, clock=self._clock, ids=self._ids
            ).record_into(uow)
            superseded = current.mark_superseded(
                revision, clock=self._clock, ids=self._ids
            ).record_into(uow)
            await uow.reports.add(revision)
            await uow.reports.save(superseded)
            await uow.commit()
        await _enqueue_triage(self._task_queue, revision.id)
        return ReportDetail.for_reporter(revision)


class WithdrawReportHandler:
    """Take a report back with a reason; the reporter only.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: ReportsUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a reports unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: WithdrawReport) -> ReportDetail:
        """Withdraw the report; a no-op if it already is.

        Args:
            command: The validated command.

        Returns:
            The reporter's exact view of the withdrawn report.

        Raises:
            ReportNotFoundError: If the report does not exist.
            PermissionDeniedError: If the actor is not the reporter.
            PreconditionFailedError: If ``expected_version`` is stale.
            ReportImmutableError: If the report was superseded.
        """
        async with self._uow_factory() as uow:
            report = await _load_report(uow, command.report_id)
            require_allowed(
                reporter_policy(report.reporter_id),
                command.actor,
                action="withdraw this report",
            )
            _check_version(command.expected_version, report.version)
            change = report.withdraw(command.reason, clock=self._clock, ids=self._ids)
            if change.events:
                report = change.record_into(uow)
                await uow.reports.save(report)
            await uow.commit()
        return ReportDetail.for_reporter(report)


class RunTriageHandler:
    """Run the triage chain over one report and store its suggestions.

    A system task (``reports.run_triage``), so there is no actor and no policy: it
    is never routed from the API, and it only attaches suggestions that change
    neither the report's content nor its status. Delivery is at least once, so the
    handler tolerates repeats: a report that is no longer current (revised or
    withdrawn meanwhile) is skipped, and an unchanged result commits no event.

    Implements: Command Handler, Chain of Responsibility.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: ReportsUnitOfWorkFactory,
        nearby_reports: NearbyReportsFinder,
        photos: PhotoEvidenceProvider,
        clock: Clock,
        ids: IdGenerator,
        chain: TriageChain | None = None,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a reports unit of work per call.
            nearby_reports: Finds candidate duplicates.
            photos: Supplies the EXIF facts of the report's photos.
            clock: Source of the evaluation time and timestamps.
            ids: Source of event ids.
            chain: The triage chain; ``TriageChain.default()`` if omitted.
        """
        self._uow_factory = uow_factory
        self._nearby_reports = nearby_reports
        self._photos = photos
        self._clock = clock
        self._ids = ids
        self._chain = TriageChain.default() if chain is None else chain

    async def __call__(self, command: RunTriage) -> TriageResult | None:
        """Triage the report and attach the result.

        Args:
            command: The report to triage.

        Returns:
            The result, or ``None`` if the report is no longer current.

        Raises:
            ReportNotFoundError: If the report does not exist.
        """
        async with self._uow_factory() as uow:
            report = await _load_report(uow, command.report_id)
            if not report.is_current:
                return None
            nearby = await self._nearby_reports.find_nearby(
                FindNearbyReports(
                    center=report.observation.coordinates,
                    observed_at=report.observed_at.value,
                    exclude_report_id=report.id,
                    radius_metres=DUPLICATE_DISTANCE_METRES,
                    window=DUPLICATE_TIME_WINDOW,
                    limit=NEARBY_REPORTS_MAX,
                )
            )
            photos = (
                await self._photos.photos_for(report.media_ids, report.reporter_id)
                if report.media_ids
                else ()
            )
            result = self._chain.run(
                TriageContext(
                    report=report,
                    nearby_reports=nearby,
                    photos=photos,
                    now=self._clock.now(),
                )
            )
            change = report.attach_triage(result, clock=self._clock, ids=self._ids)
            if change.events:
                await uow.reports.save(change.record_into(uow))
            await uow.commit()
        return result
