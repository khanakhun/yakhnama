"""A real ``create_app`` application wired to in-memory fakes, for HTTP tests.

``build_test_app`` replaces every port the routers reach in the production
container (``build_container``, which does no I/O) with a fake: the module units of
work and query services, the token validator (backed by the session's in-memory
signing key), the rate limiter and the idempotency store. Middlewares, exception
handlers and routers are the production ones, so a test exercises the whole HTTP
stack without a database or a network.

The Phase 3 modules (provenance, reports, media, events, verification, impact
claims) run their **real** command handlers and authorised query services over
in-memory units of work. Where a use case needs another module, a small bridge in
this file answers from that module's in-memory store, the way the composition
root's adapters answer from the real facades; storage, EXIF, MIME sniffing and the
task queue are the module fakes. Until ``Container`` has fields for these use
cases, ``HarnessContainer`` layers them over the built container on
``app.state.container``.

``auth_headers`` returns an ``Authorization`` header with a token signed by the same
key, so the validator accepts it::

    api = build_test_app(hazard_types=[HazardTypeTestFactory.build()])
    async with api.client() as client:
        response = await client.get("/api/v1/me", headers=auth_headers())

Patterns: Fake.
"""

import dataclasses
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI

from tests.fakes.auth import (
    DEFAULT_ISSUED_AT,
    TEST_AUDIENCE,
    TEST_ISSUER,
    FakeJwksClient,
    InMemoryIdempotencyStore,
    StaticRateLimiter,
    access_token_claims,
    issue_token,
    session_key_pair,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.events import InMemoryEventQueryService, InMemoryEventsUnitOfWork
from tests.fakes.geography import (
    InMemoryGeographyUnitOfWork,
    InMemoryPlaceQueryService,
)
from tests.fakes.hazards import (
    InMemoryHazardsUnitOfWork,
    InMemoryHazardTypeQueryService,
)
from tests.fakes.identity import (
    InMemoryIdentityQueryService,
    InMemoryIdentityUnitOfWork,
)
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import (
    InMemoryImpactMetricQueryService,
    InMemoryImpactQueryService,
    InMemoryImpactsUnitOfWork,
)
from tests.fakes.media import (
    FakeExifReader,
    FakeMimeSniffer,
    FakeStoragePort,
    InMemoryMediaQueryService,
    InMemoryMediaUnitOfWork,
)
from tests.fakes.provenance import (
    InMemoryProvenanceUnitOfWork,
    InMemorySourceQueryService,
)
from tests.fakes.reports import InMemoryReportQueryService, InMemoryReportsUnitOfWork
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.verification import (
    InMemoryVerificationQueryService,
    InMemoryVerificationUnitOfWork,
)
from yakhnama.main import create_app
from yakhnama.modules.events.domain.specifications import EventSearchCandidate
from yakhnama.modules.events.public import (
    EventDetail,
    EventHandlerDependencies,
    EventRecordQueryService,
    EventStatus,
    EventSummary,
    ReportForEvent,
    TimelineEntry,
    TimelineEntryKind,
)
from yakhnama.modules.events.public import (
    moderation_policy as events_moderation_policy,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.identity.public import Actor, Role
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.public import (
    EventImpactsQueryService,
    ImpactClaimHandlerDependencies,
    SourceTypeName,
)
from yakhnama.modules.impacts.public import (
    moderation_policy as impacts_moderation_policy,
)
from yakhnama.modules.media.public import (
    AuthorisedMediaQueryService,
    CompleteUploadHandler,
    ModerateMediaHandler,
    RequestUploadHandler,
)
from yakhnama.modules.provenance.public import (
    AuthorisedSourceQueryService,
    MarkSourceReferenced,
    MarkSourceReferencedHandler,
    RegisterSourceHandler,
    SourceRegistrar,
    SourceType,
)
from yakhnama.modules.reports.public import (
    AuthorisedReportQueryService,
    ReviseReportHandler,
    SubmitReportHandler,
    WithdrawReportHandler,
)
from yakhnama.modules.verification.public import (
    OpenVerificationCase,
    OpenVerificationCaseHandler,
    TargetKind,
    VerificationCaseQueryService,
    VerificationHandlerDependencies,
    VerificationTarget,
)
from yakhnama.modules.verification.public import (
    moderation_policy as verification_moderation_policy,
)
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.container import Container, build_container
from yakhnama.platform.ratelimit.limiter import RateLimiter
from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

TEST_SUBJECT: Final = "api-test-subject"
"""The ``sub`` of ``auth_headers`` tokens unless told otherwise."""
TEST_BASE_URL: Final = "http://localhost"
VERIFIED_STATE: Final = "verified"
_REVIEWER_ROLES: Final = frozenset({Role.MODERATOR, Role.ADMIN})
_SOURCE_TYPE_NAMES: Final[Mapping[SourceType, SourceTypeName]] = {
    SourceType.CITIZEN: "citizen",
    SourceType.ORGANISATION: "organisation",
    SourceType.GOVERNMENT: "government",
    SourceType.NEWS: "news",
    SourceType.SATELLITE: "satellite",
    SourceType.RESEARCH: "research",
    SourceType.DATASET: "dataset",
}


def harness_settings() -> Settings:
    """Return settings that ignore the environment and any ``.env`` file.

    Returns:
        Test settings; OIDC stays unset because the validator is replaced.
    """
    return Settings(_env_file=None, environment="test", log_format="console")


# --------------------------------------------------------------------------- #
# Bridges between the modules' in-memory stores                               #
# --------------------------------------------------------------------------- #


class MediaOwnershipBridge:
    """``MediaOwnershipChecker`` (reports) answering from the media store.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, media: InMemoryMediaUnitOfWork) -> None:
        """Create the bridge.

        Args:
            media: The media unit of work whose committed assets are read.
        """
        self._media = media

    async def is_owned_by(
        self, media_ids: Sequence[EntityId], owner_id: EntityId
    ) -> bool:
        """Tell whether every asset exists and was uploaded by ``owner_id``.

        Args:
            media_ids: The attached assets.
            owner_id: The reporter.

        Returns:
            ``True`` if all are the reporter's.
        """
        assets = self._media.media_assets.committed
        return all(
            media_id in assets and assets[media_id].owner_id == owner_id
            for media_id in media_ids
        )


class ReportLookupBridge:
    """Report facts for media, events and verification, from the reports store.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, reports: InMemoryReportsUnitOfWork) -> None:
        """Create the bridge.

        Args:
            reports: The reports unit of work whose committed reports are read.
        """
        self._reports = reports

    async def find_source_for_reporter(
        self, report_id: EntityId, reporter_id: EntityId
    ) -> EntityId | None:
        """Return the report's source if ``reporter_id`` submitted it (media port).

        Args:
            report_id: The report.
            reporter_id: The would-be uploader.

        Returns:
            The source id, or ``None``.
        """
        report = self._reports.reports.committed.get(report_id)
        if report is None or report.reporter_id != reporter_id:
            return None
        return report.source_id

    async def get_many(
        self, report_ids: Sequence[EntityId]
    ) -> Sequence[ReportForEvent]:
        """Return the known reports as the events module sees them (events port).

        Args:
            report_ids: The reports asked for.

        Returns:
            The known ones, with their exact points (the events handlers round).
        """
        committed = self._reports.reports.committed
        return [
            ReportForEvent(
                id=report.id,
                observed_at=report.observed_at,
                coordinates=report.observation.coordinates,
                source_id=report.source_id,
            )
            for report in (
                committed[report_id]
                for report_id in report_ids
                if report_id in committed
            )
        ]

    async def reporter_of(self, report_id: EntityId) -> EntityId | None:
        """Return who submitted a report (verification port).

        Args:
            report_id: The report.

        Returns:
            The reporter, or ``None``.
        """
        report = self._reports.reports.committed.get(report_id)
        return None if report is None else report.reporter_id


class SourceMarkerBridge:
    """Marks sources referenced for events and impacts through provenance.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, marker: MarkSourceReferencedHandler) -> None:
        """Create the bridge.

        Args:
            marker: The real provenance handler over the in-memory store.
        """
        self._marker = marker

    async def mark_referenced(
        self, source_ids: Sequence[EntityId], *, actor: Actor
    ) -> None:
        """Mark every source referenced (events port).

        Args:
            source_ids: The sources.
            actor: The acting moderator.
        """
        for source_id in source_ids:
            await self._marker(MarkSourceReferenced(actor=actor, source_id=source_id))


class ImpactSourceMarkerBridge:
    """``ImpactSourceMarker`` (impacts) over the provenance handler.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, marker: MarkSourceReferencedHandler) -> None:
        """Create the bridge.

        Args:
            marker: The real provenance handler over the in-memory store.
        """
        self._marker = marker

    async def mark_referenced(
        self, source_id: EntityId, *, actor: Actor
    ) -> SourceTypeName:
        """Mark the source referenced and return its type.

        Args:
            source_id: The source.
            actor: The acting moderator.

        Returns:
            The source's type.
        """
        detail = await self._marker(
            MarkSourceReferenced(actor=actor, source_id=source_id)
        )
        return _SOURCE_TYPE_NAMES[detail.source_type]


class VerificationCaseOpenerBridge:
    """``VerificationCaseOpener`` (events) over the verification handler.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, dependencies: VerificationHandlerDependencies) -> None:
        """Create the bridge.

        Args:
            dependencies: The verification module's ports.
        """
        self._handler = OpenVerificationCaseHandler(dependencies)

    async def open_for_event(self, event_id: EntityId, *, actor: Actor) -> None:
        """Open the event's case unless it has one.

        Args:
            event_id: The new event.
            actor: The moderator who created it.
        """
        await self._handler(
            OpenVerificationCase(
                actor=actor,
                target=VerificationTarget(kind=TargetKind.EVENT, target_id=event_id),
                if_absent=True,
            )
        )


class PlaceDirectoryBridge:
    """``PlaceDirectory`` (events) answering from the geography store.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, geography: InMemoryGeographyUnitOfWork) -> None:
        """Create the bridge.

        Args:
            geography: The geography unit of work whose committed places are read.
        """
        self._geography = geography

    async def exists(self, place_code: str) -> bool:
        """Tell whether a place has this code.

        Args:
            place_code: The code.

        Returns:
            ``True`` if known.
        """
        return place_code in self._geography.places.committed_by_code()


class ReviewerBridge:
    """``ReviewerEligibility`` (verification): moderators and admins may review.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, identity: InMemoryIdentityUnitOfWork) -> None:
        """Create the bridge.

        Args:
            identity: The identity unit of work whose committed users are read.
        """
        self._identity = identity

    async def can_review(self, user_id: EntityId) -> bool:
        """Tell whether the user holds a reviewing role.

        Args:
            user_id: The would-be reviewer.

        Returns:
            ``True`` for a known moderator or admin.
        """
        user = self._identity.users.committed.get(user_id)
        return user is not None and bool(user.roles & _REVIEWER_ROLES)


class MirroredEventQueryService(InMemoryEventQueryService):
    """``EventQueryService`` whose verification states mirror the verification store.

    The SQL query service joins the verification read model; this fake copies the
    current state of every event case before each read.

    Implements: Fake (of Query Service).
    """

    def __init__(
        self,
        events: InMemoryEventsUnitOfWork,
        verification: InMemoryVerificationUnitOfWork,
    ) -> None:
        """Create the query service.

        Args:
            events: The events unit of work whose committed rows are served.
            verification: The verification unit of work mirrored into the states.
        """
        super().__init__(events)
        self._verification = verification

    def _mirror(self) -> None:
        for case in self._verification.verification_cases.committed.values():
            if case.target.kind is TargetKind.EVENT:
                self.verification_states[case.target.target_id] = case.state.value

    async def search(
        self,
        specification: Specification[EventSearchCandidate],
        page: PageRequest,
    ) -> Page[EventSummary]:
        """Mirror the states, then search like the parent.

        Args:
            specification: The filter tree.
            page: Page size and cursor.

        Returns:
            One page of summaries.
        """
        self._mirror()
        return await super().search(specification, page)

    async def get(self, event_id: EntityId) -> EventDetail | None:
        """Mirror the states, then read like the parent.

        Args:
            event_id: The event.

        Returns:
            The detail view, or ``None``.
        """
        self._mirror()
        return await super().get(event_id)


class HazardEventDirectoryBridge:
    """``HazardEventDirectory`` (impacts) answering from the events read side.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(self, events: MirroredEventQueryService) -> None:
        """Create the bridge.

        Args:
            events: The mirrored events query service.
        """
        self._events = events

    async def exists(self, event_id: EntityId) -> bool:
        """Tell whether the event exists.

        Args:
            event_id: The event.

        Returns:
            ``True`` if known.
        """
        return await self._events.get(event_id) is not None

    async def is_publicly_visible(self, event_id: EntityId) -> bool:
        """Tell whether the event is published and verified.

        Args:
            event_id: The event.

        Returns:
            ``True`` if anyone may read it.
        """
        detail = await self._events.get(event_id)
        return (
            detail is not None
            and detail.status is EventStatus.PUBLISHED
            and detail.verification_state == VERIFIED_STATE
        )


class TimelineBridge:
    """``EventTimelineSources`` (events) from the verification and impacts stores.

    Implements: Fake (of the composition root's Adapter).
    """

    def __init__(
        self,
        verification: InMemoryVerificationUnitOfWork,
        impacts: InMemoryImpactsUnitOfWork,
    ) -> None:
        """Create the bridge.

        Args:
            verification: Supplies the event's case history.
            impacts: Supplies the event's claims.
        """
        self._verification = verification
        self._impacts = impacts

    async def verification_transitions(
        self, event_id: EntityId
    ) -> Sequence[TimelineEntry]:
        """Return one entry per transition of the event's case.

        Args:
            event_id: The event.

        Returns:
            The entries, labelled with the state reached.
        """
        return [
            TimelineEntry(
                kind=TimelineEntryKind.VERIFICATION_TRANSITION,
                at=DateWithPrecision(
                    value=transition.occurred_at, precision=DatePrecision.EXACT
                ),
                subject_id=case.id,
                label=transition.to_state.value,
            )
            for case in self._verification.verification_cases.committed.values()
            if case.target.kind is TargetKind.EVENT
            and case.target.target_id == event_id
            for transition in case.history
        ]

    async def impact_claims(self, event_id: EntityId) -> Sequence[TimelineEntry]:
        """Return one entry per claim of the event, labelled with its metric.

        Args:
            event_id: The event.

        Returns:
            The entries.
        """
        return [
            TimelineEntry(
                kind=TimelineEntryKind.IMPACT_CLAIM,
                at=claim.claimed_at,
                subject_id=claim.id,
                label=claim.metric.code,
            )
            for claim in self._impacts.impact_claims.committed.values()
            if claim.event_id == event_id
        ]


# --------------------------------------------------------------------------- #
# The Phase 3 bindings and the container layered over them                    #
# --------------------------------------------------------------------------- #


# Frozen dataclasses, like the container: they hold live fakes, not data.
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordingServices:
    """The Phase 3 use cases the routers read from ``app.state.container``.

    Each attribute is a field the lead adds to ``Container``, with the same name.

    Implements: Fake (of the composition root's bindings).

    Attributes:
        source_registrar: Registers sources (provenance).
        source_queries: Authorised source reads.
        submit_report_handler: Submits reports.
        revise_report_handler: Revises reports.
        withdraw_report_handler: Withdraws reports.
        report_queries: Authorised report reads.
        request_upload_handler: Grants presigned uploads.
        complete_upload_handler: Completes uploads.
        moderate_media_handler: Moderates media.
        media_queries: Authorised media reads.
        event_handler_dependencies: What every events handler is built from.
        event_queries: Authorised events reads.
        verification_handler_dependencies: What every verification handler is
            built from.
        verification_queries: Authorised verification reads.
        impact_claim_handler_dependencies: What every claims handler is built from.
        event_impacts_queries: Authorised impacts reads.
    """

    source_registrar: SourceRegistrar
    source_queries: AuthorisedSourceQueryService
    submit_report_handler: SubmitReportHandler
    revise_report_handler: ReviseReportHandler
    withdraw_report_handler: WithdrawReportHandler
    report_queries: AuthorisedReportQueryService
    request_upload_handler: RequestUploadHandler
    complete_upload_handler: CompleteUploadHandler
    moderate_media_handler: ModerateMediaHandler
    media_queries: AuthorisedMediaQueryService
    event_handler_dependencies: EventHandlerDependencies
    event_queries: EventRecordQueryService
    verification_handler_dependencies: VerificationHandlerDependencies
    verification_queries: VerificationCaseQueryService
    impact_claim_handler_dependencies: ImpactClaimHandlerDependencies
    event_impacts_queries: EventImpactsQueryService


class HarnessContainer:
    """The built ``Container`` with the Phase 3 bindings layered over it.

    Attribute reads try ``recording`` first, then the container, so every router
    sees one object with all the services, as it will once ``Container`` has the
    fields.

    Implements: Fake (of the composition root's bindings).
    """

    __slots__ = ("_base", "_recording")

    def __init__(self, base: Container, recording: RecordingServices) -> None:
        """Layer the bindings.

        Args:
            base: The container the app was built with.
            recording: The Phase 3 bindings.
        """
        self._base = base
        self._recording = recording

    def __getattr__(self, name: str) -> object:
        """Return a Phase 3 binding, else the container's attribute.

        Args:
            name: The attribute.

        Returns:
            Its value.
        """
        if name in RecordingServices.__dataclass_fields__:
            return getattr(self._recording, name)
        return getattr(self._base, name)


# A frozen dataclass, like the container it wraps: it holds live fakes to arrange
# and inspect, not data that is validated or serialised.
@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ApiHarness:
    """The wired application and the fakes behind it.

    Implements: Fake (of the composition root's bindings).

    Attributes:
        app: The application built by ``create_app``.
        hazards: The hazards unit of work; its repositories hold the data.
        impacts: The impacts unit of work (metrics, claims, assets, damage).
        geography: The geography unit of work.
        identity: The identity unit of work.
        provenance: The provenance unit of work.
        reports: The reports unit of work.
        media: The media unit of work.
        events: The events unit of work.
        verification: The verification unit of work.
        storage: The object storage fake.
        exif_reader: The EXIF reader fake.
        mime_sniffer: The MIME sniffer fake.
        task_queue: The task queue fake; records every enqueued task.
        services: The Phase 3 bindings.
        idempotency_store: The idempotency store the middleware uses.
        rate_limiter: The rate limiter the middleware uses.
        clock: The clock of the container and the token validator.
    """

    app: FastAPI
    hazards: InMemoryHazardsUnitOfWork
    impacts: InMemoryImpactsUnitOfWork
    geography: InMemoryGeographyUnitOfWork
    identity: InMemoryIdentityUnitOfWork
    provenance: InMemoryProvenanceUnitOfWork
    reports: InMemoryReportsUnitOfWork
    media: InMemoryMediaUnitOfWork
    events: InMemoryEventsUnitOfWork
    verification: InMemoryVerificationUnitOfWork
    storage: FakeStoragePort
    exif_reader: FakeExifReader
    mime_sniffer: FakeMimeSniffer
    task_queue: RecordingTaskQueue
    services: RecordingServices
    idempotency_store: InMemoryIdempotencyStore
    rate_limiter: RateLimiter
    clock: FrozenClock

    @asynccontextmanager
    async def client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an HTTP client calling the app in process.

        Yields:
            The client, with a trusted ``Host``.
        """
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url=TEST_BASE_URL
        ) as client:
            yield client


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class _Stores:
    """The Phase 3 in-memory stores and adapter fakes, built together.

    Implements: Fake.
    """

    provenance: InMemoryProvenanceUnitOfWork
    reports: InMemoryReportsUnitOfWork
    media: InMemoryMediaUnitOfWork
    events: InMemoryEventsUnitOfWork
    verification: InMemoryVerificationUnitOfWork
    storage: FakeStoragePort
    exif_reader: FakeExifReader
    mime_sniffer: FakeMimeSniffer
    task_queue: RecordingTaskQueue


def build_recording_services(  # noqa: PLR0913  # reason: one argument per store it wires
    *,
    stores: _Stores,
    container: Container,
    impacts: InMemoryImpactsUnitOfWork,
    geography: InMemoryGeographyUnitOfWork,
    identity: InMemoryIdentityUnitOfWork,
    settings: Settings,
) -> RecordingServices:
    """Wire the Phase 3 handlers and query services over the in-memory stores.

    Args:
        stores: The Phase 3 stores and adapter fakes.
        container: Supplies the clock, ids and hazard type query service.
        impacts: The impacts unit of work.
        geography: The geography unit of work, for place codes.
        identity: The identity unit of work, for reviewer eligibility.
        settings: Supplies ``public_coordinate_decimals``.

    Returns:
        The bindings.
    """
    clock, ids = container.clock, container.id_generator
    coordinates = PublicCoordinatePolicy(decimals=settings.public_coordinate_decimals)
    provenance_factory = InMemoryUnitOfWorkFactory(stores.provenance)
    registrar = RegisterSourceHandler(provenance_factory, clock, ids)
    marker = MarkSourceReferencedHandler(provenance_factory, clock, ids)
    reports_factory = InMemoryUnitOfWorkFactory(stores.reports)
    media_factory = InMemoryUnitOfWorkFactory(stores.media)
    report_lookup = ReportLookupBridge(stores.reports)
    verification_dependencies = VerificationHandlerDependencies(
        uow_factory=InMemoryUnitOfWorkFactory(stores.verification),
        policy=verification_moderation_policy(),
        clock=clock,
        ids=ids,
        report_owners=report_lookup,
        reviewers=ReviewerBridge(identity),
    )
    event_reads = MirroredEventQueryService(stores.events, stores.verification)
    impacts_factory = InMemoryUnitOfWorkFactory(impacts)
    events_directory = HazardEventDirectoryBridge(event_reads)
    return RecordingServices(
        source_registrar=registrar,
        source_queries=AuthorisedSourceQueryService(
            InMemorySourceQueryService(stores.provenance)
        ),
        submit_report_handler=SubmitReportHandler(
            uow_factory=reports_factory,
            source_registrar=registrar,
            source_marker=marker,
            media_checker=MediaOwnershipBridge(stores.media),
            task_queue=stores.task_queue,
            clock=clock,
            ids=ids,
        ),
        revise_report_handler=ReviseReportHandler(
            uow_factory=reports_factory,
            media_checker=MediaOwnershipBridge(stores.media),
            task_queue=stores.task_queue,
            clock=clock,
            ids=ids,
        ),
        withdraw_report_handler=WithdrawReportHandler(reports_factory, clock, ids),
        report_queries=AuthorisedReportQueryService(
            InMemoryReportQueryService(stores.reports), coordinates
        ),
        request_upload_handler=RequestUploadHandler(
            uow_factory=media_factory,
            storage=stores.storage,
            report_sources=report_lookup,
            source_registrar=registrar,
            source_marker=marker,
            clock=clock,
            ids=ids,
        ),
        complete_upload_handler=CompleteUploadHandler(
            uow_factory=media_factory,
            storage=stores.storage,
            exif_reader=stores.exif_reader,
            mime_sniffer=stores.mime_sniffer,
            task_queue=stores.task_queue,
            clock=clock,
            ids=ids,
        ),
        moderate_media_handler=ModerateMediaHandler(
            uow_factory=media_factory, storage=stores.storage, clock=clock, ids=ids
        ),
        media_queries=AuthorisedMediaQueryService(
            InMemoryMediaQueryService(stores.media), stores.storage
        ),
        event_handler_dependencies=EventHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(stores.events),
            policy=events_moderation_policy(),
            clock=clock,
            ids=ids,
            reports=report_lookup,
            sources=SourceMarkerBridge(marker),
            cases=VerificationCaseOpenerBridge(verification_dependencies),
            places=PlaceDirectoryBridge(geography),
            hazard_types=container.hazard_type_query_service,
            coordinates=coordinates,
        ),
        event_queries=EventRecordQueryService(
            event_reads,
            policy=events_moderation_policy(),
            reports=report_lookup,
            timeline_sources=TimelineBridge(stores.verification, impacts),
        ),
        verification_handler_dependencies=verification_dependencies,
        verification_queries=VerificationCaseQueryService(
            InMemoryVerificationQueryService(stores.verification.verification_cases),
            verification_moderation_policy(),
        ),
        impact_claim_handler_dependencies=ImpactClaimHandlerDependencies(
            uow_factory=impacts_factory,
            policy=impacts_moderation_policy(),
            clock=clock,
            ids=ids,
            events=events_directory,
            sources=ImpactSourceMarkerBridge(marker),
        ),
        event_impacts_queries=EventImpactsQueryService(
            uow_factory=impacts_factory,
            reads=InMemoryImpactQueryService(impacts),
            events=events_directory,
            policy=impacts_moderation_policy(),
            clock=clock,
        ),
    )


def build_test_app(  # noqa: PLR0913  # reason: one optional seed per fake repository
    *,
    settings: Settings | None = None,
    hazard_types: Iterable[HazardType] = (),
    impact_metrics: Iterable[ImpactMetric] = (),
    places: Iterable[Place] = (),
    users: Iterable[User] = (),
    organizations: Iterable[Organization] = (),
    memberships: Iterable[Membership] = (),
    rate_limiter: RateLimiter | None = None,
) -> ApiHarness:
    """Build the app with every port the routers use bound to a fake.

    Args:
        settings: The settings; ``harness_settings()`` when ``None``.
        hazard_types: Hazard types that exist before the test acts.
        impact_metrics: Impact metrics that exist before the test acts.
        places: Places that exist before the test acts.
        users: Users that exist before the test acts.
        organizations: Organisations that exist before the test acts.
        memberships: Memberships that exist before the test acts.
        rate_limiter: The limiter; an always-allowing ``StaticRateLimiter`` when
            ``None``.

    Returns:
        The application and its fakes.
    """
    resolved_settings = settings if settings is not None else harness_settings()
    clock = FrozenClock(DEFAULT_ISSUED_AT)
    hazards = InMemoryHazardsUnitOfWork(hazard_types)
    impacts = InMemoryImpactsUnitOfWork(impact_metrics)
    geography = InMemoryGeographyUnitOfWork(places)
    identity = InMemoryIdentityUnitOfWork(
        users=users, organizations=organizations, memberships=memberships
    )
    idempotency_store = InMemoryIdempotencyStore()
    limiter = rate_limiter if rate_limiter is not None else StaticRateLimiter()
    container = dataclasses.replace(
        build_container(resolved_settings),
        clock=clock,
        id_generator=SequentialIdGenerator(),
        token_validator=TokenValidator(
            jwks_client=FakeJwksClient([session_key_pair()]),
            issuer=TEST_ISSUER,
            audience=TEST_AUDIENCE,
            algorithms=["RS256"],
            leeway_seconds=0,
            clock=clock,
        ),
        rate_limiter=limiter,
        idempotency_store=idempotency_store,
        hazards_uow_factory=InMemoryUnitOfWorkFactory(hazards),
        impacts_uow_factory=InMemoryUnitOfWorkFactory(impacts),
        geography_uow_factory=InMemoryUnitOfWorkFactory(geography),
        identity_uow_factory=InMemoryUnitOfWorkFactory(identity),
        hazard_type_query_service=InMemoryHazardTypeQueryService(hazards.hazard_types),
        impact_metric_query_service=InMemoryImpactMetricQueryService(
            impacts.impact_metrics
        ),
        place_query_service=InMemoryPlaceQueryService(geography.places),
        identity_query_service=InMemoryIdentityQueryService(identity),
    )
    stores = _Stores(
        provenance=InMemoryProvenanceUnitOfWork(),
        reports=InMemoryReportsUnitOfWork(),
        media=InMemoryMediaUnitOfWork(),
        events=InMemoryEventsUnitOfWork(),
        verification=InMemoryVerificationUnitOfWork(),
        storage=FakeStoragePort(),
        exif_reader=FakeExifReader(),
        mime_sniffer=FakeMimeSniffer(),
        task_queue=RecordingTaskQueue(),
    )
    services = build_recording_services(
        stores=stores,
        container=container,
        impacts=impacts,
        geography=geography,
        identity=identity,
        settings=resolved_settings,
    )
    app = create_app(resolved_settings, container)
    app.state.container = HarnessContainer(container, services)
    return ApiHarness(
        app=app,
        hazards=hazards,
        impacts=impacts,
        geography=geography,
        identity=identity,
        provenance=stores.provenance,
        reports=stores.reports,
        media=stores.media,
        events=stores.events,
        verification=stores.verification,
        storage=stores.storage,
        exif_reader=stores.exif_reader,
        mime_sniffer=stores.mime_sniffer,
        task_queue=stores.task_queue,
        services=services,
        idempotency_store=idempotency_store,
        rate_limiter=limiter,
        clock=clock,
    )


def auth_headers(
    *,
    subject: str = TEST_SUBJECT,
    roles: Iterable[str] = (),
    name: str | None = None,
) -> dict[str, str]:
    """Return an ``Authorization`` header the test app's validator accepts.

    Args:
        subject: The token's ``sub``; one subject is one mirrored user.
        roles: Realm role names, for example ``["moderator"]``.
        name: The ``name`` claim, when given.

    Returns:
        ``{"Authorization": "Bearer <token>"}``.
    """
    claims = access_token_claims(subject=subject, realm_roles=roles, name=name)
    return {"Authorization": f"Bearer {issue_token(claims, session_key_pair())}"}
