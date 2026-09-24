"""A real ``create_app`` application wired to in-memory fakes, for HTTP tests.

``build_test_app`` replaces every port the routers reach in the production
container (``build_container``, which does no I/O) with a fake: the module units of
work and query services, the token validator (backed by the session's in-memory
signing key), the rate limiter and the idempotency store. Middlewares, exception
handlers and routers are the production ones, so a test exercises the whole HTTP
stack without a database or a network.

The Phase 3 modules (provenance, reports, media, events, verification, impact
claims, audit) are wired by the production ``build_recording_services`` over
in-memory units of work and query services, so the real command handlers,
authorised query services **and** cross-module adapters of
``yakhnama.platform.wiring`` run; storage, EXIF, MIME sniffing and the task queue
are the module fakes. The result is laid over the built container with
``dataclasses.replace``, so a renamed ``Container`` field breaks these tests.

``auth_headers`` returns an ``Authorization`` header with a token signed by the same
key, so the validator accepts it::

    api = build_test_app(hazard_types=[HazardTypeTestFactory.build()])
    async with api.client() as client:
        response = await client.get("/api/v1/me", headers=auth_headers())

Patterns: Fake.
"""

import dataclasses
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI

from tests.fakes.audit import InMemoryAuditQueryService, InMemoryAuditUnitOfWork
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
from tests.fakes.reports import (
    FakeNearbyReportsFinder,
    InMemoryReportQueryService,
    InMemoryReportsUnitOfWork,
)
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.verification import (
    InMemoryVerificationQueryService,
    InMemoryVerificationUnitOfWork,
)
from yakhnama.main import create_app
from yakhnama.modules.events.domain.specifications import EventSearchCandidate
from yakhnama.modules.events.public import EventDetail, EventSummary
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.identity.domain.entities import Membership, Organization, User
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    NoOpMalwareScanner,
)
from yakhnama.modules.media.public import ScanStatus
from yakhnama.modules.verification.public import TargetKind
from yakhnama.platform.auth.tokens import TokenValidator
from yakhnama.platform.container import (
    Container,
    CorePorts,
    MediaAdapters,
    RecordingReads,
    RecordingServices,
    RecordingUnits,
    build_container,
    build_recording_services,
)
from yakhnama.platform.ratelimit.limiter import RateLimiter
from yakhnama.platform.settings import Settings
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.specification import Specification

TEST_SUBJECT: Final = "api-test-subject"
"""The ``sub`` of ``auth_headers`` tokens unless told otherwise."""
TEST_BASE_URL: Final = "http://localhost"


def harness_settings() -> Settings:
    """Return settings that ignore the environment and any ``.env`` file.

    Returns:
        Test settings; OIDC stays unset because the validator is replaced.
    """
    return Settings(_env_file=None, environment="test", log_format="console")


# --------------------------------------------------------------------------- #
# The read side the SQL events query service joins                           #
# --------------------------------------------------------------------------- #


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
        services: The Phase 3 bindings, as laid over the container.
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
    audit: InMemoryAuditUnitOfWork
    storage: FakeStoragePort
    exif_reader: FakeExifReader
    mime_sniffer: FakeMimeSniffer
    task_queue: RecordingTaskQueue


def build_recording_services_over_fakes(  # noqa: PLR0913  # reason: one argument per store it wires
    *,
    stores: _Stores,
    container: Container,
    impacts: InMemoryImpactsUnitOfWork,
    geography: InMemoryGeographyUnitOfWork,
    identity: InMemoryIdentityUnitOfWork,
    settings: Settings,
) -> tuple[RecordingUnits, RecordingReads, MediaAdapters, RecordingServices]:
    """Wire the Phase 3 use cases with the production builder over the fakes.

    Args:
        stores: The Phase 3 stores and adapter fakes.
        container: Supplies the clock, ids and hazard type query service.
        impacts: The impacts unit of work (claims share it with the metrics).
        geography: The geography unit of work, for place codes.
        identity: The identity unit of work, for reviewer eligibility.
        settings: Supplies ``public_coordinate_decimals``.

    Returns:
        The unit-of-work factories, read ports, media adapters and use cases.
    """
    core = CorePorts(
        clock=container.clock,
        id_generator=container.id_generator,
        public_coordinates=PublicCoordinatePolicy(
            decimals=settings.public_coordinate_decimals
        ),
        identity_uow_factory=InMemoryUnitOfWorkFactory(identity),
        geography_uow_factory=InMemoryUnitOfWorkFactory(geography),
        hazard_type_query_service=container.hazard_type_query_service,
    )
    units = RecordingUnits(
        provenance=InMemoryUnitOfWorkFactory(stores.provenance),
        reports=InMemoryUnitOfWorkFactory(stores.reports),
        media=InMemoryUnitOfWorkFactory(stores.media),
        events=InMemoryUnitOfWorkFactory(stores.events),
        verification=InMemoryUnitOfWorkFactory(stores.verification),
        impact_claims=InMemoryUnitOfWorkFactory(impacts),
        audit=InMemoryUnitOfWorkFactory(stores.audit),
    )
    reads = RecordingReads(
        sources=InMemorySourceQueryService(stores.provenance),
        reports=InMemoryReportQueryService(stores.reports),
        nearby_reports=FakeNearbyReportsFinder(),
        media=InMemoryMediaQueryService(stores.media),
        events=MirroredEventQueryService(stores.events, stores.verification),
        verification=InMemoryVerificationQueryService(
            stores.verification.verification_cases
        ),
        impacts=InMemoryImpactQueryService(impacts),
        audit=InMemoryAuditQueryService(stores.audit),
    )
    media = MediaAdapters(
        storage=stores.storage,
        exif_reader=stores.exif_reader,
        mime_sniffer=stores.mime_sniffer,
        # Clean, so a test can publish media; the fake storage holds no bytes.
        malware_scanner=NoOpMalwareScanner(verdict=ScanStatus.CLEAN),
    )
    services = build_recording_services(
        core=core,
        units=units,
        reads=reads,
        media=media,
        task_queue=stores.task_queue,
    )
    return units, reads, media, services


def lay_recording_over(  # noqa: PLR0913  # reason: one argument per group laid over the container
    container: Container,
    *,
    units: RecordingUnits,
    reads: RecordingReads,
    media: MediaAdapters,
    services: RecordingServices,
    task_queue: RecordingTaskQueue,
) -> Container:
    """Return ``container`` with every Phase 3 field replaced.

    Args:
        container: The container to copy.
        units: The replacement unit-of-work factories.
        reads: The replacement read ports.
        media: The replacement media adapters.
        services: The replacement use cases.
        task_queue: The replacement task queue.

    Returns:
        The copy; ``dataclasses.replace`` fails on a field ``Container`` lacks.
    """
    return dataclasses.replace(
        container,
        provenance_uow_factory=units.provenance,
        reports_uow_factory=units.reports,
        media_uow_factory=units.media,
        events_uow_factory=units.events,
        verification_uow_factory=units.verification,
        impact_claims_uow_factory=units.impact_claims,
        audit_uow_factory=units.audit,
        source_query_service=reads.sources,
        report_query_service=reads.reports,
        nearby_reports_finder=reads.nearby_reports,
        media_query_service=reads.media,
        event_query_service=reads.events,
        verification_query_service=reads.verification,
        impact_query_service=reads.impacts,
        audit_query_service=reads.audit,
        storage=media.storage,
        exif_reader=media.exif_reader,
        mime_sniffer=media.mime_sniffer,
        malware_scanner=media.malware_scanner,
        source_registrar=services.source_registrar,
        source_queries=services.source_queries,
        submit_report_handler=services.submit_report_handler,
        revise_report_handler=services.revise_report_handler,
        withdraw_report_handler=services.withdraw_report_handler,
        report_queries=services.report_queries,
        run_triage_handler=services.run_triage_handler,
        request_upload_handler=services.request_upload_handler,
        complete_upload_handler=services.complete_upload_handler,
        moderate_media_handler=services.moderate_media_handler,
        record_scan_result_handler=services.record_scan_result_handler,
        media_queries=services.media_queries,
        event_handler_dependencies=services.event_handler_dependencies,
        event_queries=services.event_queries,
        verification_handler_dependencies=services.verification_handler_dependencies,
        verification_queries=services.verification_queries,
        impact_claim_handler_dependencies=services.impact_claim_handler_dependencies,
        event_impacts_queries=services.event_impacts_queries,
        task_queue=task_queue,
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
        audit=InMemoryAuditUnitOfWork(),
        storage=FakeStoragePort(),
        exif_reader=FakeExifReader(),
        mime_sniffer=FakeMimeSniffer(),
        task_queue=RecordingTaskQueue(),
    )
    units, reads, media, services = build_recording_services_over_fakes(
        stores=stores,
        container=container,
        impacts=impacts,
        geography=geography,
        identity=identity,
        settings=resolved_settings,
    )
    container = lay_recording_over(
        container,
        units=units,
        reads=reads,
        media=media,
        services=services,
        task_queue=stores.task_queue,
    )
    app = create_app(resolved_settings, container)
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
