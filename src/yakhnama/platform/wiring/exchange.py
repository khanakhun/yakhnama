"""Adapters answering the exchange module's ports from other modules' facades.

- ``IdentityActorLookupAdapter`` (``ActorLookup``): rebuilds a job's requesting
  actor from the identity users and memberships when the job runs, so a user
  suspended or stripped of a role meanwhile gets nothing.
- ``FacadeExportRowSource`` (``ExportRowSource``): pages through the authorised
  events, impacts and reports read services, so an export applies exactly the
  visibility rules of the API for the rebuilt actor.
- ``RegistryReferenceCheckerAdapter`` (``BackfillReferenceChecker``): asks the
  hazards, impacts and geography read sides whether a row's codes resolve.
- ``ProvenanceLineageRegistrarAdapter`` (``LineageSourceRegistrar``): registers the
  import's ``dataset`` source through the provenance ``RegisterSourceHandler``.
- ``SessionBatchEventWriter`` (``HistoricalEventWriter``) and ``SessionBatchScope``
  (``BatchScope``): one import batch in one database transaction; see below.
- ``RunExportTaskAdapter`` and ``RunImportTaskAdapter``: the ``exchange.run_export``
  and ``exchange.run_import`` task handlers.

**Batch atomicity.** ``SessionBatchEventWriter.batch`` opens one connection and one
transaction per batch and builds, for that batch only, the provenance, events,
verification and impact claims handlers over units of work whose sessions *join*
that transaction (SQLAlchemy ``join_transaction_mode="create_savepoint"``): each
unit of work's ``commit`` releases a savepoint and writes its outbox rows inside the
batch transaction, and its rollback returns to its savepoint. Nothing is durable
until ``SessionBatchScope.commit`` commits the connection's transaction; leaving the
block without it rolls back every source, event, case, claim and outbox row of the
batch. The events read port the claims handler checks events with is bound to the
same transaction, so it sees the event the batch has just created. Reference reads
(hazard types, places, reviewers, report facts) use the application's ordinary
ports, which read committed data only.

**Row sources.** The backfill contract has no source-type column, so every row's
own source is registered as ``dataset`` (**proposed**, open question): the figure
reached the platform through a curated file, and a moderator can register the
original publication separately. Its title is derived from the row's title,
because the contract carries only a citation.

Internal imports: ``SqlAlchemyEventQueryService``,
``SqlAlchemyEventsUnitOfWork``, ``SqlAlchemyImpactClaimsUnitOfWork``,
``SqlAlchemyProvenanceUnitOfWork`` and ``SqlAlchemyVerificationUnitOfWork`` (the
module infrastructure a batch rebinds to its transaction; binding them is the
composition root's job), ``GeographyUnitOfWorkFactory`` (as in ``wiring.events``)
and the identity ``IdentityUnitOfWorkFactory`` exported by the identity facade.

Patterns: Adapter.
"""

import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker

from yakhnama.modules.events.infrastructure.queries import SqlAlchemyEventQueryService
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.events.public import (
    CreateHistoricalEvent,
    CreateHistoricalEventHandler,
    EventDetail,
    EventGeometry,
    EventHandlerDependencies,
    EventPeriod,
    EventRecordQueryService,
    EventStatus,
    GetEvent,
    ListEvents,
)
from yakhnama.modules.events.public import (
    moderation_policy as events_moderation_policy,
)
from yakhnama.modules.exchange.public import (
    BatchScope,
    ClaimExportRow,
    EventExportRow,
    ExportFilters,
    ImportedEventDraft,
    LineageSource,
    ReportExportRow,
    RowIssue,
    RunExport,
    RunExportHandler,
    RunImport,
    RunImportHandler,
)
from yakhnama.modules.geography.application.ports import GeographyUnitOfWorkFactory
from yakhnama.modules.hazards.public import (
    HazardTypeQueryService,
    HazardTypeRef,
    HazardTypeStatus,
)
from yakhnama.modules.identity.public import (
    Actor,
    IdentityUnitOfWork,
    IdentityUnitOfWorkFactory,
)
from yakhnama.modules.impacts.infrastructure.claims_uow import (
    SqlAlchemyImpactClaimsUnitOfWork,
)
from yakhnama.modules.impacts.public import (
    ClaimValue,
    EventImpactsQueryService,
    ImpactClaimHandlerDependencies,
    ImpactMetricQueryService,
    ListClaims,
    MetricStatus,
    MonetaryValue,
    RecordImpactClaim,
    RecordImpactClaimHandler,
)
from yakhnama.modules.impacts.public import (
    moderation_policy as impacts_moderation_policy,
)
from yakhnama.modules.provenance.infrastructure.uow import (
    SqlAlchemyProvenanceUnitOfWork,
)
from yakhnama.modules.provenance.public import (
    MarkSourceReferencedHandler,
    RegisterSource,
    RegisterSourceHandler,
    SourceDetails,
    SourceRegistrar,
    SourceType,
)
from yakhnama.modules.reports.public import (
    AuthorisedReportQueryService,
    ListReports,
    ReportStatus,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.modules.verification.public import (
    OpenVerificationCaseHandler,
    VerificationHandlerDependencies,
)
from yakhnama.modules.verification.public import (
    moderation_policy as verification_moderation_policy,
)
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.uow import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from yakhnama.platform.wiring.events import (
    EventSourceMarkerAdapter,
    PlaceDirectoryAdapter,
    ReportFactsAdapter,
    VerificationCaseOpenerAdapter,
)
from yakhnama.platform.wiring.impacts import (
    HazardEventDirectoryAdapter,
    ImpactSourceMarkerAdapter,
)
from yakhnama.platform.wiring.verification import (
    ReportOwnerAdapter,
    ReviewerEligibilityAdapter,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import InvariantViolationError, ValidationError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.pagination import MAX_PAGE_LIMIT, PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.tasks import ScheduledTask
from yakhnama.shared_kernel.value_objects import DateWithPrecision

ROW_SOURCE_TYPE: Final = SourceType.DATASET
"""Source type of every imported row's own source (**proposed**; module docs)."""

ROW_SOURCE_TITLE_PREFIX: Final = "Source cited by the imported record"
"""Start of a row source's title; the row's title follows (**proposed**)."""

UNKNOWN_HAZARD_MESSAGE: Final = "the hazard type is unknown or retired"
UNKNOWN_METRIC_MESSAGE: Final = "the impact metric is unknown or retired"
METRIC_KIND_MESSAGE: Final = "the value kind does not match the impact metric"
METRIC_UNIT_MESSAGE: Final = "the unit or currency does not match the impact metric"
UNKNOWN_PLACE_MESSAGE: Final = "a place code does not exist"


# --------------------------------------------------------------------------- #
# Actors                                                                      #
# --------------------------------------------------------------------------- #


class IdentityActorLookupAdapter:
    """``ActorLookup`` over the identity users, memberships and organisations.

    Builds the actor as the identity module does for a request: the user's roles
    and the memberships of organisations that are active. The identity facade
    exports no use case for this yet, so the few lines are repeated here (open
    question for the identity owner).

    Implements: Adapter.
    """

    def __init__(self, identity: IdentityUnitOfWorkFactory) -> None:
        """Create the adapter.

        Args:
            identity: Opens an identity unit of work; this adapter only reads.
        """
        self._identity = identity

    async def actor_for(self, user_id: EntityId) -> Actor | None:
        """Return the current actor of a user.

        Args:
            user_id: The user.

        Returns:
            The actor, or ``None`` if the user does not exist or is suspended.
        """
        # Read-only: the unit of work is never committed, so leaving it rolls back.
        async with self._identity() as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.is_active:
                return None
            memberships = [
                membership
                for membership in await uow.memberships.list_for_user(user.id)
                if await _is_active_organization(uow, membership.organization_id)
            ]
        return user.to_actor(memberships)


async def _is_active_organization(
    uow: IdentityUnitOfWork, organization_id: EntityId
) -> bool:
    # Memberships of suspended or retired organisations grant nothing, as in the
    # identity module's own actor resolution.
    organization = await uow.organizations.get(organization_id)
    return organization is not None and organization.is_active


# --------------------------------------------------------------------------- #
# Export rows                                                                 #
# --------------------------------------------------------------------------- #


def _earliest(moment: DateWithPrecision | None) -> DateWithPrecision | None:
    return None if moment is None else moment.truncate()


def _period_from(filters: ExportFilters) -> EventPeriod | None:
    if filters.occurred_from is None:
        return None
    return EventPeriod(started_at=filters.occurred_from)


def _period_to(filters: ExportFilters) -> EventPeriod | None:
    if filters.occurred_to is None:
        return None
    return EventPeriod(started_at=filters.occurred_to)


def _status_of[StatusT: (EventStatus, ReportStatus)](
    status_type: type[StatusT], value: str | None
) -> StatusT | None:
    if value is None:
        return None
    try:
        return status_type(value)
    except ValueError:
        message = "the status filter is not a status of this dataset"
        raise ValidationError(
            message, details={"field": "status", "code": value}
        ) from None


class FacadeExportRowSource:
    """``ExportRowSource`` over the authorised events, impacts and reports reads.

    Every page is read through the same query service the API uses, with the
    rebuilt actor, so a non-moderator exports only published and verified events,
    only the claims of events they may read, and reports with rounded positions.
    The time filters are read at their precision: from the first instant of
    ``occurred_from``'s period to the last instant of ``occurred_to``'s.

    Events are listed as summaries and then read one by one for the fields a
    summary lacks (geometry, summary, sources); an export is a background job, so
    the extra reads cost time, not a request's latency.

    Reports have no place filter in their read service; ``place_code`` is matched
    against the reporter's place hint here (**proposed**).

    Implements: Adapter.
    """

    def __init__(
        self,
        *,
        events: EventRecordQueryService,
        impacts: EventImpactsQueryService,
        reports: AuthorisedReportQueryService,
    ) -> None:
        """Create the adapter.

        Args:
            events: The authorised events reads.
            impacts: The authorised impact claim reads.
            reports: The authorised report reads.
        """
        self._events = events
        self._impacts = impacts
        self._reports = reports

    async def _event_ids(
        self, filters: ExportFilters, actor: Actor
    ) -> AsyncIterator[EntityId]:
        period_from = _period_from(filters)
        period_to = _period_to(filters)
        status = _status_of(EventStatus, filters.status)
        cursor: str | None = None
        while True:
            page = await self._events.list_events(
                ListEvents(
                    actor=actor,
                    bbox=filters.bbox,
                    hazard_type=filters.hazard_type,
                    place_code=filters.place_code,
                    status=status,
                    period_from=(
                        None if period_from is None else period_from.earliest_instant()
                    ),
                    period_to=(
                        None if period_to is None else period_to.latest_instant()
                    ),
                    page=PageRequest(limit=MAX_PAGE_LIMIT, cursor=cursor),
                )
            )
            for summary in page.items:
                yield summary.id
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    async def events(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[EventExportRow]:
        """Yield every event matching ``filters`` that ``actor`` may see.

        Args:
            filters: The export's filters.
            actor: The requesting user, rebuilt at run time.

        Yields:
            The rows, in the events read service's order (newest first, then id).

        Raises:
            ValidationError: If ``filters.status`` is not an event status.
        """
        async for event_id in self._event_ids(filters, actor):
            detail = await self._events.get_event(
                GetEvent(actor=actor, event_id=event_id)
            )
            yield _event_row(detail)

    async def claims(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[ClaimExportRow]:
        """Yield every claim of the events matching ``filters`` that ``actor`` sees.

        Args:
            filters: The export's filters, applied to the claims' events.
            actor: The requesting user, rebuilt at run time.

        Yields:
            The rows, event by event in the events order, each event's claims in
            the claims read service's order; retracted claims included.

        Raises:
            ValidationError: If ``filters.status`` is not an event status.
        """
        async for event_id in self._event_ids(filters, actor):
            cursor: str | None = None
            while True:
                page = await self._impacts.list_claims(
                    ListClaims(
                        actor=actor,
                        event_id=event_id,
                        include_retracted=True,
                        page=PageRequest(limit=MAX_PAGE_LIMIT, cursor=cursor),
                    )
                )
                for claim in page.items:
                    yield ClaimExportRow(
                        claim_id=claim.id,
                        event_id=claim.event_id,
                        metric_code=claim.metric_code,
                        value=claim.value,
                        confidence=claim.confidence,
                        source_id=claim.source_id,
                        source_type=claim.source_type,
                        claimed_at=claim.claimed_at,
                        scope=claim.scope,
                        status=claim.status,
                        supersedes_id=claim.supersedes_id,
                        created_at=claim.created_at,
                    )
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor

    async def reports(
        self, filters: ExportFilters, *, actor: Actor
    ) -> AsyncIterator[ReportExportRow]:
        """Yield every report matching ``filters``, positions rounded.

        Args:
            filters: The export's filters.
            actor: The requesting moderator, rebuilt at run time.

        Yields:
            The rows, newest first, as the reports read service orders them.

        Raises:
            ValidationError: If ``filters.status`` is not a report status.
        """
        status = _status_of(ReportStatus, filters.status)
        period_to = _period_to(filters)
        observed_from = _earliest(filters.occurred_from)
        cursor: str | None = None
        while True:
            page = await self._reports.list_reports(
                ListReports(
                    actor=actor,
                    status=status,
                    hazard_code=filters.hazard_type,
                    bbox=filters.bbox,
                    observed_from=None
                    if observed_from is None
                    else observed_from.value,
                    observed_to=(
                        None if period_to is None else period_to.latest_instant()
                    ),
                    page=PageRequest(limit=MAX_PAGE_LIMIT, cursor=cursor),
                )
            )
            for report in page.items:
                if filters.place_code is not None and (
                    report.place_hint != filters.place_code
                ):
                    continue
                yield ReportExportRow(
                    report_id=report.id,
                    status=report.status,
                    revision=report.revision,
                    observed_at=report.observed_at,
                    coordinates=report.coordinates,
                    hazard_code=report.hazard_code,
                    place_hint=report.place_hint,
                    media_count=report.media_count,
                    submitted_at=report.submitted_at,
                )
            if page.next_cursor is None:
                return
            cursor = page.next_cursor


def _event_row(detail: EventDetail) -> EventExportRow:
    return EventExportRow(
        event_id=detail.id,
        hazard_code=detail.hazard_code,
        title=detail.title,
        summary=detail.summary,
        started_at=detail.period.started_at,
        ended_at=detail.period.ended_at,
        centroid=detail.centroid,
        geometry=(
            None if detail.geometry is None else EventGeometry(geojson=detail.geometry)
        ),
        place_codes=tuple(
            sorted({place.place_code for place in detail.affected_places})
        ),
        source_ids=detail.source_ids,
        status=detail.status,
        verification_state=detail.verification_state,
        updated_at=detail.updated_at,
    )


# --------------------------------------------------------------------------- #
# Reference checks                                                            #
# --------------------------------------------------------------------------- #


class RegistryReferenceCheckerAdapter:
    """``BackfillReferenceChecker`` over the hazards, impacts and geography reads.

    The same rules as the handlers that will write the row: the hazard type must
    exist and be active (as ``CreateHistoricalEvent`` requires), each claim's
    metric must exist, be active and match the value's kind and unit or currency
    (as ``RecordImpactClaim`` requires), and each place code must exist. Messages
    are fixed sentences; the code itself is never quoted.

    Implements: Adapter.
    """

    def __init__(
        self,
        *,
        hazard_types: HazardTypeQueryService,
        impact_metrics: ImpactMetricQueryService,
        places: PlaceDirectoryAdapter,
    ) -> None:
        """Create the adapter.

        Args:
            hazard_types: The hazards read port.
            impact_metrics: The impact metric read port.
            places: Tells whether a place code exists.
        """
        self._hazard_types = hazard_types
        self._impact_metrics = impact_metrics
        self._places = places

    async def check(
        self, row_number: int, draft: ImportedEventDraft
    ) -> tuple[RowIssue, ...]:
        """Return every reference problem of one draft.

        Args:
            row_number: The draft's 1-based data row.
            draft: The draft.

        Returns:
            The issues in column order, each naming its column; empty if every
            reference resolves.
        """
        issues: list[RowIssue] = []
        hazard_type = await self._hazard_types.get(draft.hazard_type)
        if hazard_type is None or hazard_type.status is not HazardTypeStatus.ACTIVE:
            issues.append(
                RowIssue(
                    row_number=row_number,
                    field="hazard_type",
                    message=UNKNOWN_HAZARD_MESSAGE,
                )
            )
        for place_code in draft.place_codes:
            if not await self._places.exists(place_code):
                issues.append(
                    RowIssue(
                        row_number=row_number,
                        field="place_codes",
                        message=UNKNOWN_PLACE_MESSAGE,
                    )
                )
                # One issue per column is enough to reject the row; which code is
                # missing is not quoted anyway.
                break
        for slot, claim in enumerate(draft.claims, start=1):
            issue = await self._check_claim(
                row_number, slot, claim.metric_code, claim.value
            )
            if issue is not None:
                issues.append(issue)
        return tuple(issues)

    async def _check_claim(
        self,
        row_number: int,
        slot: int,
        metric_code: str,
        value: ClaimValue,
    ) -> RowIssue | None:
        metric = await self._impact_metrics.get(metric_code)
        if metric is None or metric.status is not MetricStatus.ACTIVE:
            return RowIssue(
                row_number=row_number,
                field=f"claim_{slot}_metric_code",
                message=UNKNOWN_METRIC_MESSAGE,
            )
        if value.value_kind is not metric.value_kind:
            return RowIssue(
                row_number=row_number,
                field=f"claim_{slot}_value_kind",
                message=METRIC_KIND_MESSAGE,
            )
        is_monetary = isinstance(value, MonetaryValue)
        expected = metric.currency if is_monetary else metric.unit
        if value.unit_or_currency != expected:
            return RowIssue(
                row_number=row_number,
                field=f"claim_{slot}_{'currency' if is_monetary else 'unit'}",
                message=METRIC_UNIT_MESSAGE,
            )
        return None


# --------------------------------------------------------------------------- #
# Lineage source                                                              #
# --------------------------------------------------------------------------- #


class ProvenanceLineageRegistrarAdapter:
    """``LineageSourceRegistrar`` over the provenance ``SourceRegistrar``.

    Implements: Adapter.
    """

    def __init__(self, registrar: SourceRegistrar) -> None:
        """Create the adapter.

        Args:
            registrar: The provenance ``RegisterSourceHandler``.
        """
        self._registrar = registrar

    async def register(self, source: LineageSource, *, actor: Actor) -> EntityId:
        """Register the import's ``dataset`` source.

        Args:
            source: What to register.
            actor: The requesting moderator.

        Returns:
            The new source's id.

        Raises:
            PermissionDeniedError: If the actor may not register dataset sources.
        """
        detail = await self._registrar(
            RegisterSource(
                actor=actor, source_type=SourceType.DATASET, details=source.details
            )
        )
        return detail.id


# --------------------------------------------------------------------------- #
# Import batches                                                              #
# --------------------------------------------------------------------------- #


def row_source_details(draft: ImportedEventDraft) -> SourceDetails:
    """Return the provenance details of an imported row's own source.

    Args:
        draft: The validated row.

    Returns:
        The row's citation, URL and licence, titled after the row (**proposed**).
    """
    return SourceDetails(
        title=f"{ROW_SOURCE_TITLE_PREFIX}: {draft.title}",
        citation=draft.source.citation,
        url=draft.source.url,
        licence=draft.source.licence,
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BatchReferencePorts:
    """The ports a batch reads committed reference data through.

    A frozen dataclass like the container: it groups live ports, not data.

    Implements: Composition Root.

    Attributes:
        outbox_writer: Stages each unit of work's domain events.
        clock: Source of timestamps.
        ids: Source of ids.
        coordinates: The published-position rounding.
        hazard_types: Hazard type reads (the event's hazard code).
        geography: Opens geography units of work (place codes).
        identity: Opens identity units of work (reviewer eligibility).
        report_facts: Report facts for the events dependencies (unused by the
            historical creation, which links no report).
        report_owners: Report owners for the verification dependencies.
    """

    outbox_writer: OutboxWriter
    clock: Clock
    ids: IdGenerator
    coordinates: PublicCoordinatePolicy
    hazard_types: HazardTypeQueryService
    geography: GeographyUnitOfWorkFactory
    identity: IdentityUnitOfWorkFactory
    report_facts: ReportFactsAdapter
    report_owners: ReportOwnerAdapter


class SessionBatchScope:
    """``BatchScope`` whose every write joins one connection's transaction.

    Implements: Unit of Work.
    """

    def __init__(
        self,
        *,
        commit: Callable[[], Awaitable[None]],
        registrar: RegisterSourceHandler,
        create_event: CreateHistoricalEventHandler,
        record_claim: RecordImpactClaimHandler,
    ) -> None:
        """Create the scope.

        Args:
            commit: Commits the batch's transaction (the connection's
                ``commit``).
            registrar: Registers the rows' own sources in the transaction.
            create_event: Creates historical events in the transaction.
            record_claim: Records impact claims in the transaction.
        """
        self._commit = commit
        self._registrar = registrar
        self._create_event = create_event
        self._record_claim = record_claim
        self._is_committed = False

    @property
    def is_committed(self) -> bool:
        """Tell whether ``commit`` ran."""
        return self._is_committed

    async def create(
        self,
        draft: ImportedEventDraft,
        *,
        lineage_source_id: EntityId,
        actor: Actor,
    ) -> EntityId:
        """Write one historical event with its own source and its claims.

        Args:
            draft: The validated row.
            lineage_source_id: The import's ``dataset`` source.
            actor: The requesting moderator.

        Returns:
            The new event's id.

        Raises:
            InvariantViolationError: If the batch has already committed.
            YakhnamaError: If any write is refused; the batch must then be left
                without ``commit``.
        """
        if self._is_committed:
            message = "the import batch has already committed; open a new one"
            raise InvariantViolationError(message)
        row_source = await self._registrar(
            RegisterSource(
                actor=actor,
                source_type=ROW_SOURCE_TYPE,
                details=row_source_details(draft),
            )
        )
        created = await self._create_event(
            CreateHistoricalEvent(
                actor=actor,
                title=draft.title,
                hazard_type=HazardTypeRef(code=draft.hazard_type),
                period=draft.period,
                geometry=draft.geometry,
                place_codes=draft.place_codes,
                summary=draft.summary,
                source_ids=(row_source.id, lineage_source_id),
            )
        )
        for claim in draft.claims:
            await self._record_claim(
                RecordImpactClaim(
                    actor=actor,
                    event_id=created.id,
                    metric_code=claim.metric_code,
                    value=claim.value,
                    confidence=claim.confidence,
                    source_id=row_source.id,
                    claimed_at=claim.claimed_at,
                    note=claim.note,
                )
            )
        return created.id

    async def commit(self) -> None:
        """Commit every write of the batch at once.

        Raises:
            InvariantViolationError: If the batch has already committed.
        """
        if self._is_committed:
            message = "the import batch has already committed; open a new one"
            raise InvariantViolationError(message)
        await self._commit()
        self._is_committed = True


class SessionBatchEventWriter:
    """``HistoricalEventWriter`` opening one database transaction per batch.

    See the module docs for how the modules' units of work join it.

    Implements: Adapter.
    """

    def __init__(self, engine: AsyncEngine, references: BatchReferencePorts) -> None:
        """Create the writer; no connection is opened until a batch starts.

        Args:
            engine: The application's engine; each batch takes one connection.
            references: The ports a batch reads committed reference data through.
        """
        self._engine = engine
        self._references = references

    @asynccontextmanager
    async def batch(self) -> AsyncIterator[BatchScope]:
        """Open one batch.

        Yields:
            The scope; leaving the block without ``commit`` rolls back every
            write made through it.
        """
        async with self._engine.connect() as connection:
            await connection.begin()
            scope = self._scope(connection)
            try:
                yield scope
            finally:
                if not scope.is_committed:
                    await connection.rollback()

    def _scope(self, connection: AsyncConnection) -> SessionBatchScope:
        refs = self._references
        # Sessions on the batch connection: each unit of work's commit releases a
        # savepoint inside the batch transaction instead of committing it.
        sessions = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        def factory[UnitOfWorkT: SqlAlchemyUnitOfWork](
            unit_of_work_class: type[UnitOfWorkT],
        ) -> SqlAlchemyUnitOfWorkFactory[UnitOfWorkT]:
            return SqlAlchemyUnitOfWorkFactory(
                unit_of_work_class,
                session_factory=sessions,
                outbox_writer=refs.outbox_writer,
            )

        provenance = factory(SqlAlchemyProvenanceUnitOfWork)
        registrar = RegisterSourceHandler(provenance, refs.clock, refs.ids)
        marker = MarkSourceReferencedHandler(provenance, refs.clock, refs.ids)
        verification = VerificationHandlerDependencies(
            uow_factory=factory(SqlAlchemyVerificationUnitOfWork),
            policy=verification_moderation_policy(),
            clock=refs.clock,
            ids=refs.ids,
            report_owners=refs.report_owners,
            reviewers=ReviewerEligibilityAdapter(refs.identity),
        )
        events = EventHandlerDependencies(
            uow_factory=factory(SqlAlchemyEventsUnitOfWork),
            policy=events_moderation_policy(),
            clock=refs.clock,
            ids=refs.ids,
            reports=refs.report_facts,
            sources=EventSourceMarkerAdapter(marker),
            cases=VerificationCaseOpenerAdapter(
                OpenVerificationCaseHandler(verification)
            ),
            places=PlaceDirectoryAdapter(refs.geography),
            hazard_types=refs.hazard_types,
            coordinates=refs.coordinates,
        )
        claims = ImpactClaimHandlerDependencies(
            uow_factory=factory(SqlAlchemyImpactClaimsUnitOfWork),
            policy=impacts_moderation_policy(),
            clock=refs.clock,
            ids=refs.ids,
            # Bound to the batch transaction, so it sees the event just created.
            events=HazardEventDirectoryAdapter(SqlAlchemyEventQueryService(sessions)),
            sources=ImpactSourceMarkerAdapter(marker),
        )
        return SessionBatchScope(
            commit=connection.commit,
            registrar=registrar,
            create_event=CreateHistoricalEventHandler(events),
            record_claim=RecordImpactClaimHandler(claims),
        )


# --------------------------------------------------------------------------- #
# Tasks                                                                       #
# --------------------------------------------------------------------------- #


class ExportTaskPayload(BaseModel):
    """The payload of ``exchange.run_export``, validated from the broker's JSON.

    Implements: DTO.

    Attributes:
        export_job_id: The export job to run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    export_job_id: EntityId


class ImportTaskPayload(BaseModel):
    """The payload of ``exchange.run_import``, validated from the broker's JSON.

    Implements: DTO.

    Attributes:
        import_job_id: The import job to run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    import_job_id: EntityId


class RunExportTaskAdapter:
    """The ``exchange.run_export`` task handler over ``RunExportHandler``.

    Implements: Adapter.
    """

    def __init__(self, handler: RunExportHandler) -> None:
        """Create the task handler.

        Args:
            handler: The export run use case.
        """
        self._handler = handler

    async def __call__(self, task: ScheduledTask) -> None:
        """Run the export named in the payload.

        Args:
            task: The task; payload ``{export_job_id}``.

        Raises:
            pydantic.ValidationError: If the payload is not an export payload.
            ExportJobNotFoundError: If the job does not exist.
        """
        payload = ExportTaskPayload.model_validate(dict(task.payload))
        await self._handler(RunExport(job_id=payload.export_job_id))


class RunImportTaskAdapter:
    """The ``exchange.run_import`` task handler over ``RunImportHandler``.

    Implements: Adapter.
    """

    def __init__(self, handler: RunImportHandler) -> None:
        """Create the task handler.

        Args:
            handler: The import run use case.
        """
        self._handler = handler

    async def __call__(self, task: ScheduledTask) -> None:
        """Run the import named in the payload.

        Args:
            task: The task; payload ``{import_job_id}``.

        Raises:
            pydantic.ValidationError: If the payload is not an import payload.
            ImportJobNotFoundError: If the job does not exist.
        """
        payload = ImportTaskPayload.model_validate(dict(task.payload))
        await self._handler(RunImport(job_id=payload.import_job_id))
