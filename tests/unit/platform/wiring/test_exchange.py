"""Unit tests for ``yakhnama.platform.wiring.exchange`` over the module fakes.

The read-side adapters run over the production Phase 3 use cases built by
``build_recording_services`` on in-memory stores, so the authorised query services
apply their real visibility rules. The batch writer's transaction handling needs a
database and is covered by ``tests/integration/wiring``; its scope's mapping from
a draft to the three module commands is tested here over the in-memory handlers.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import pydantic
import pytest

from tests.factories.events import AffectedPlaceTestFactory, EventTestFactory
from tests.factories.geography import PlaceTestFactory
from tests.factories.hazards import HazardTypeTestFactory
from tests.factories.identity import (
    MembershipTestFactory,
    OrganizationTestFactory,
    UserTestFactory,
)
from tests.factories.impacts import ImpactClaimTestFactory, ImpactMetricTestFactory
from tests.factories.reports import ReportTestFactory
from tests.fakes.api import (
    RecordingStores,
    build_recording_services_over_fakes,
    build_test_app,
)
from tests.fakes.audit import InMemoryAuditUnitOfWork
from tests.fakes.clock import FrozenClock
from tests.fakes.events import InMemoryEventsUnitOfWork
from tests.fakes.geography import InMemoryGeographyUnitOfWork
from tests.fakes.hazards import (
    InMemoryHazardsUnitOfWork,
    InMemoryHazardTypeQueryService,
)
from tests.fakes.identity import InMemoryIdentityUnitOfWork, actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import (
    InMemoryImpactMetricQueryService,
    InMemoryImpactsUnitOfWork,
)
from tests.fakes.media import (
    FakeExifReader,
    FakeMimeSniffer,
    FakeStoragePort,
    InMemoryMediaUnitOfWork,
)
from tests.fakes.provenance import InMemoryProvenanceUnitOfWork
from tests.fakes.reports import InMemoryReportsUnitOfWork
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.verification import InMemoryVerificationUnitOfWork
from yakhnama.modules.events.public import (
    CreateHistoricalEventHandler,
    EventGeometry,
    EventPeriod,
    EventStatus,
)
from yakhnama.modules.exchange.domain.backfill import (
    ImportedClaimDraft,
    ImportedSourceDraft,
)
from yakhnama.modules.exchange.public import (
    ExportFilters,
    ExportJobNotFoundError,
    ImportedEventDraft,
    ImportJobNotFoundError,
    LineageSource,
    RunExportHandler,
    RunImportHandler,
)
from yakhnama.modules.hazards.domain.value_objects import RetirementReason
from yakhnama.modules.identity.domain.value_objects import (
    OrganizationStatus,
    UserStatus,
)
from yakhnama.modules.identity.public import Role
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.public import (
    CountValue,
    MonetaryValue,
    RecordImpactClaimHandler,
    ValueKind,
)
from yakhnama.modules.provenance.public import (
    RegisterSource,
    RegisterSourceHandler,
    SourceDetails,
    SourceType,
)
from yakhnama.platform.container import CorePorts, RecordingServices
from yakhnama.platform.wiring.events import PlaceDirectoryAdapter
from yakhnama.platform.wiring.exchange import (
    METRIC_KIND_MESSAGE,
    METRIC_UNIT_MESSAGE,
    ROW_SOURCE_TITLE_PREFIX,
    ROW_SOURCE_TYPE,
    UNKNOWN_HAZARD_MESSAGE,
    UNKNOWN_METRIC_MESSAGE,
    UNKNOWN_PLACE_MESSAGE,
    FacadeExportRowSource,
    IdentityActorLookupAdapter,
    ProvenanceLineageRegistrarAdapter,
    RegistryReferenceCheckerAdapter,
    RunExportTaskAdapter,
    RunImportTaskAdapter,
    SessionBatchScope,
    row_source_details,
)
from yakhnama.shared_kernel.errors import InvariantViolationError, ValidationError
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId
from yakhnama.shared_kernel.value_objects import (
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

IDS: Final = SequentialIdGenerator(seed=9401)
CLOCK: Final = FrozenClock(datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
MODERATOR: Final = actor_with({Role.MODERATOR}, user_id=IDS.new_id())
CITIZEN: Final = actor_with(user_id=IDS.new_id())
HUNZA: Final = "pk.gb.hunza"
NAGAR: Final = "pk.gb.nagar"
DECIMALS: Final = 2


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def _core(
    *,
    identity: InMemoryIdentityUnitOfWork | None = None,
    hazards: InMemoryHazardsUnitOfWork | None = None,
    geography: InMemoryGeographyUnitOfWork | None = None,
) -> CorePorts:
    hazard_store = hazards if hazards is not None else InMemoryHazardsUnitOfWork()
    return CorePorts(
        clock=CLOCK,
        id_generator=IDS,
        public_coordinates=PublicCoordinatePolicy(decimals=DECIMALS),
        identity_uow_factory=InMemoryUnitOfWorkFactory(
            identity if identity is not None else InMemoryIdentityUnitOfWork()
        ),
        geography_uow_factory=InMemoryUnitOfWorkFactory(
            geography if geography is not None else InMemoryGeographyUnitOfWork()
        ),
        hazard_type_query_service=InMemoryHazardTypeQueryService(
            hazard_store.hazard_types
        ),
    )


def _stores(
    *,
    events: InMemoryEventsUnitOfWork | None = None,
    reports: InMemoryReportsUnitOfWork | None = None,
    provenance: InMemoryProvenanceUnitOfWork | None = None,
) -> RecordingStores:
    return RecordingStores(
        provenance=(
            provenance if provenance is not None else InMemoryProvenanceUnitOfWork()
        ),
        reports=reports if reports is not None else InMemoryReportsUnitOfWork(),
        media=InMemoryMediaUnitOfWork(),
        events=events if events is not None else InMemoryEventsUnitOfWork(),
        verification=InMemoryVerificationUnitOfWork(),
        audit=InMemoryAuditUnitOfWork(),
        storage=FakeStoragePort(),
        exif_reader=FakeExifReader(),
        mime_sniffer=FakeMimeSniffer(),
        task_queue=RecordingTaskQueue(),
    )


def _services(
    stores: RecordingStores,
    *,
    core: CorePorts | None = None,
    impacts: InMemoryImpactsUnitOfWork | None = None,
) -> RecordingServices:
    _, _, _, services = build_recording_services_over_fakes(
        stores=stores,
        core=core if core is not None else _core(),
        impacts=impacts if impacts is not None else InMemoryImpactsUnitOfWork(),
    )
    return services


def _row_source(services: RecordingServices) -> FacadeExportRowSource:
    return FacadeExportRowSource(
        events=services.event_queries,
        impacts=services.event_impacts_queries,
        reports=services.report_queries,
    )


def _day(year: int, month: int, day: int) -> DateWithPrecision:
    return DateWithPrecision(
        value=datetime(year, month, day, tzinfo=UTC), precision=DatePrecision.DAY
    )


def _draft(
    *,
    hazard_type: str = "glof",
    place_codes: tuple[str, ...] = (HUNZA,),
    claims: tuple[ImportedClaimDraft, ...] = (),
) -> ImportedEventDraft:
    return ImportedEventDraft(
        title="Outburst flood of 1974",
        hazard_type=hazard_type,
        period=EventPeriod(started_at=_day(1974, 7, 12)),
        place_codes=place_codes,
        source=ImportedSourceDraft(citation="District archive, flood register 1974"),
        claims=claims,
    )


def _claim(metric_code: str, value: CountValue | MonetaryValue) -> ImportedClaimDraft:
    return ImportedClaimDraft(
        metric_code=metric_code,
        value=value,
        confidence=Confidence.MEDIUM,
        claimed_at=_day(1974, 7, 20),
    )


def _task(task_name: str, payload: dict[str, object]) -> ScheduledTask:
    return ScheduledTask.model_validate(
        {"task_id": TaskId(value="t-1"), "task_name": task_name, "payload": payload}
    )


# --------------------------------------------------------------------------- #
# Actors                                                                      #
# --------------------------------------------------------------------------- #


async def test_identity_actor_lookup_keeps_only_active_organization_memberships() -> (
    None
):
    user = UserTestFactory.build(roles=frozenset({Role.CITIZEN, Role.MODERATOR}))
    active = OrganizationTestFactory.build()
    suspended = OrganizationTestFactory.build(
        status=OrganizationStatus.SUSPENDED, status_reason="Duplicate organisation."
    )
    memberships = [
        MembershipTestFactory.build(user_id=user.id, organization_id=active.id),
        MembershipTestFactory.build(user_id=user.id, organization_id=suspended.id),
    ]
    identity = InMemoryIdentityUnitOfWork(
        users=[user], organizations=[active, suspended], memberships=memberships
    )
    adapter = IdentityActorLookupAdapter(InMemoryUnitOfWorkFactory(identity))

    actor = await adapter.actor_for(user.id)

    assert actor is not None
    assert actor.user_id == user.id
    assert Role.MODERATOR in actor.roles
    assert {organization for organization, _ in actor.memberships} == {active.id}


async def test_identity_actor_lookup_unknown_user_returns_none() -> None:
    adapter = IdentityActorLookupAdapter(
        InMemoryUnitOfWorkFactory(InMemoryIdentityUnitOfWork())
    )

    actor = await adapter.actor_for(IDS.new_id())

    assert actor is None


async def test_identity_actor_lookup_suspended_user_returns_none() -> None:
    user = UserTestFactory.build(
        status=UserStatus.SUSPENDED, status_reason="Repeated spam reports."
    )
    adapter = IdentityActorLookupAdapter(
        InMemoryUnitOfWorkFactory(InMemoryIdentityUnitOfWork(users=[user]))
    )

    actor = await adapter.actor_for(user.id)

    assert actor is None


# --------------------------------------------------------------------------- #
# Export rows                                                                 #
# --------------------------------------------------------------------------- #


async def test_export_row_source_events_maps_detail_fields_for_a_moderator() -> None:
    geometry = EventGeometry.from_coordinates(
        Coordinates(longitude=74.6, latitude=36.3)
    )
    event = EventTestFactory.build(
        status=EventStatus.DRAFT,
        geometry=geometry,
        centroid=geometry.centroid(),
        summary="Lake drained overnight.",
        affected_places=(
            AffectedPlaceTestFactory.build(place_code=NAGAR),
            AffectedPlaceTestFactory.build(place_code=HUNZA),
        ),
    )
    source = _row_source(_services(_stores(events=InMemoryEventsUnitOfWork([event]))))

    rows = [row async for row in source.events(ExportFilters(), actor=MODERATOR)]

    assert len(rows) == 1
    row = rows[0]
    assert row.event_id == event.id
    assert row.geometry == geometry
    assert row.summary == "Lake drained overnight."
    assert row.place_codes == (HUNZA, NAGAR)
    assert row.source_ids == event.source_ids
    assert row.status is EventStatus.DRAFT
    assert row.started_at == event.period.started_at


async def test_export_row_source_events_hides_drafts_from_a_non_moderator() -> None:
    event = EventTestFactory.build(status=EventStatus.DRAFT)
    source = _row_source(_services(_stores(events=InMemoryEventsUnitOfWork([event]))))

    rows = [row async for row in source.events(ExportFilters(), actor=CITIZEN)]

    assert rows == []


async def test_export_row_source_events_applies_status_and_time_filters() -> None:
    period = EventPeriod(started_at=_day(2025, 7, 12), ended_at=_day(2025, 7, 14))
    published = EventTestFactory.build(status=EventStatus.PUBLISHED, period=period)
    draft = EventTestFactory.build(status=EventStatus.DRAFT, period=period)
    source = _row_source(
        _services(_stores(events=InMemoryEventsUnitOfWork([published, draft])))
    )
    later = 2026

    by_status = [
        row.event_id
        async for row in source.events(
            ExportFilters(status="published"), actor=MODERATOR
        )
    ]
    after_both = [
        row
        async for row in source.events(
            ExportFilters(occurred_from=_day(later, 1, 1)), actor=MODERATOR
        )
    ]
    before_both = [
        row
        async for row in source.events(
            ExportFilters(occurred_to=_day(1900, 1, 1)), actor=MODERATOR
        )
    ]

    assert by_status == [published.id]
    assert (after_both, before_both) == ([], [])


async def test_export_row_source_events_unknown_status_raises_validation_error() -> (
    None
):
    source = _row_source(_services(_stores()))

    with pytest.raises(ValidationError) as raised:
        _ = [
            row
            async for row in source.events(
                ExportFilters(status="submitted"), actor=MODERATOR
            )
        ]

    assert raised.value.details["field"] == "status"


async def test_export_row_source_events_pages_past_one_page() -> None:
    events = EventTestFactory.batch(201, status=EventStatus.DRAFT)
    source = _row_source(_services(_stores(events=InMemoryEventsUnitOfWork(events))))

    rows = [row async for row in source.events(ExportFilters(), actor=MODERATOR)]

    assert {row.event_id for row in rows} == {event.id for event in events}


async def test_export_row_source_claims_yields_every_claim_of_visible_events() -> None:
    event = EventTestFactory.build(status=EventStatus.DRAFT)
    other = EventTestFactory.build(status=EventStatus.DRAFT)
    metric = ImpactMetricTestFactory.build(
        code="test_metric_count", value_kind=ValueKind.COUNT
    )
    claims = [
        ImpactClaimTestFactory.build(event_id=event.id),
        ImpactClaimTestFactory.build(event_id=event.id),
        ImpactClaimTestFactory.build(event_id=other.id),
    ]
    services = _services(
        _stores(events=InMemoryEventsUnitOfWork([event, other])),
        impacts=InMemoryImpactsUnitOfWork([metric], claims=claims),
    )
    source = _row_source(services)

    rows = [row async for row in source.claims(ExportFilters(), actor=MODERATOR)]
    hidden = [row async for row in source.claims(ExportFilters(), actor=CITIZEN)]

    assert {row.claim_id for row in rows} == {claim.id for claim in claims}
    assert {row.event_id for row in rows} == {event.id, other.id}
    assert hidden == []


async def test_export_row_source_claims_pages_past_one_page() -> None:
    event = EventTestFactory.build(status=EventStatus.DRAFT)
    metric = ImpactMetricTestFactory.build(
        code="test_metric_count", value_kind=ValueKind.COUNT
    )
    claims = ImpactClaimTestFactory.batch(201, event_id=event.id)
    services = _services(
        _stores(events=InMemoryEventsUnitOfWork([event])),
        impacts=InMemoryImpactsUnitOfWork([metric], claims=claims),
    )

    rows = [
        row
        async for row in _row_source(services).claims(ExportFilters(), actor=MODERATOR)
    ]

    assert {row.claim_id for row in rows} == {claim.id for claim in claims}


async def test_export_row_source_reports_rounds_and_filters_by_place_hint() -> None:
    hinted = ReportTestFactory.build(place_hint=HUNZA)
    unhinted = ReportTestFactory.build(place_hint=None)
    source = _row_source(
        _services(
            _stores(reports=InMemoryReportsUnitOfWork(reports=[hinted, unhinted]))
        )
    )

    everything = [row async for row in source.reports(ExportFilters(), actor=MODERATOR)]
    in_hunza = [
        row.report_id
        async for row in source.reports(
            ExportFilters(place_code=HUNZA), actor=MODERATOR
        )
    ]

    assert {row.report_id for row in everything} == {hinted.id, unhinted.id}
    assert in_hunza == [hinted.id]
    for row in everything:
        assert round(row.coordinates.longitude, DECIMALS) == row.coordinates.longitude
        assert round(row.coordinates.latitude, DECIMALS) == row.coordinates.latitude


async def test_export_row_source_reports_applies_the_time_window() -> None:
    report = ReportTestFactory.build()
    source = _row_source(
        _services(_stores(reports=InMemoryReportsUnitOfWork(reports=[report])))
    )
    observed = report.observed_at.value

    inside = [
        row.report_id
        async for row in source.reports(
            ExportFilters(
                occurred_from=_day(observed.year, observed.month, observed.day),
                occurred_to=_day(observed.year, observed.month, observed.day),
            ),
            actor=MODERATOR,
        )
    ]
    outside = [
        row
        async for row in source.reports(
            ExportFilters(occurred_to=_day(1900, 1, 1)), actor=MODERATOR
        )
    ]

    assert (inside, outside) == ([report.id], [])


async def test_export_row_source_reports_unknown_status_raises_validation_error() -> (
    None
):
    source = _row_source(_services(_stores()))

    with pytest.raises(ValidationError):
        _ = [
            row
            async for row in source.reports(
                ExportFilters(status="published"), actor=MODERATOR
            )
        ]


async def test_export_row_source_reports_pages_past_one_page() -> None:
    reports = ReportTestFactory.batch(201)
    source = _row_source(
        _services(_stores(reports=InMemoryReportsUnitOfWork(reports=reports)))
    )

    rows = [row async for row in source.reports(ExportFilters(), actor=MODERATOR)]

    assert len(rows) == len(reports)


# --------------------------------------------------------------------------- #
# Reference checks                                                            #
# --------------------------------------------------------------------------- #


def _checker(
    *, hazard_codes: tuple[str, ...] = ("glof",), metrics: Sequence[ImpactMetric] = ()
) -> RegistryReferenceCheckerAdapter:
    hazards = InMemoryHazardsUnitOfWork(
        [HazardTypeTestFactory.build(code=code) for code in hazard_codes]
    )
    impacts = InMemoryImpactsUnitOfWork(metrics)
    geography = InMemoryGeographyUnitOfWork([PlaceTestFactory.build(code=HUNZA)])
    return RegistryReferenceCheckerAdapter(
        hazard_types=InMemoryHazardTypeQueryService(hazards.hazard_types),
        impact_metrics=InMemoryImpactMetricQueryService(impacts.impact_metrics),
        places=PlaceDirectoryAdapter(InMemoryUnitOfWorkFactory(geography)),
    )


COUNT_METRIC: Final = ImpactMetricTestFactory.build(
    code="deaths", value_kind=ValueKind.COUNT
)
MONEY_METRIC: Final = ImpactMetricTestFactory.build(
    code="economic_loss", value_kind=ValueKind.MONETARY, currency="PKR"
)


def _money(currency: str) -> MonetaryValue:
    return MonetaryValue(amount=Decimal(1000), currency=currency, price_year=2020)


async def test_reference_checker_resolving_draft_returns_no_issues() -> None:
    checker = _checker(metrics=[COUNT_METRIC, MONEY_METRIC])
    draft = _draft(
        claims=(
            _claim("deaths", CountValue(count=3)),
            _claim("economic_loss", _money("PKR")),
        )
    )

    issues = await checker.check(4, draft)

    assert issues == ()


async def test_reference_checker_reports_each_unresolved_reference_by_column() -> None:
    checker = _checker(hazard_codes=(), metrics=[COUNT_METRIC, MONEY_METRIC])
    draft = _draft(
        place_codes=(HUNZA, NAGAR, "pk.gb.skardu"),
        claims=(
            _claim("unknown_metric", CountValue(count=1)),
            _claim("economic_loss", CountValue(count=1)),
            _claim("economic_loss", _money("USD")),
        ),
    )

    issues = await checker.check(2, draft)

    assert [(issue.row_number, issue.field, issue.message) for issue in issues] == [
        (2, "hazard_type", UNKNOWN_HAZARD_MESSAGE),
        (2, "place_codes", UNKNOWN_PLACE_MESSAGE),
        (2, "claim_1_metric_code", UNKNOWN_METRIC_MESSAGE),
        (2, "claim_2_value_kind", METRIC_KIND_MESSAGE),
        (2, "claim_3_currency", METRIC_UNIT_MESSAGE),
    ]
    assert all("USD" not in issue.message for issue in issues)


async def test_reference_checker_retired_hazard_type_is_reported() -> None:
    retired = (
        HazardTypeTestFactory.build(code="glof")
        .retire(
            RetirementReason(text="Split into narrower types."), clock=CLOCK, ids=IDS
        )
        .state
    )
    hazards = InMemoryHazardsUnitOfWork([retired])
    checker = RegistryReferenceCheckerAdapter(
        hazard_types=InMemoryHazardTypeQueryService(hazards.hazard_types),
        impact_metrics=InMemoryImpactMetricQueryService(
            InMemoryImpactsUnitOfWork().impact_metrics
        ),
        places=PlaceDirectoryAdapter(
            InMemoryUnitOfWorkFactory(
                InMemoryGeographyUnitOfWork([PlaceTestFactory.build(code=HUNZA)])
            )
        ),
    )

    issues = await checker.check(1, _draft())

    assert [issue.field for issue in issues] == ["hazard_type"]


# --------------------------------------------------------------------------- #
# Lineage and row sources                                                     #
# --------------------------------------------------------------------------- #


async def test_lineage_registrar_registers_a_dataset_source() -> None:
    provenance = InMemoryProvenanceUnitOfWork()
    registrar = RegisterSourceHandler(InMemoryUnitOfWorkFactory(provenance), CLOCK, IDS)
    adapter = ProvenanceLineageRegistrarAdapter(registrar)
    details = SourceDetails(
        title="Yakhnama historical import", citation="Imported by job 1."
    )

    source_id = await adapter.register(
        LineageSource(import_job_id=IDS.new_id(), details=details), actor=MODERATOR
    )

    stored = provenance.sources.committed[source_id]
    assert stored.source_type is SourceType.DATASET
    assert stored.details == details


def test_row_source_details_title_after_the_row_and_keep_its_citation() -> None:
    draft = _draft()

    details = row_source_details(draft)

    assert details.title == f"{ROW_SOURCE_TITLE_PREFIX}: {draft.title}"
    assert details.citation == draft.source.citation
    assert (details.url, details.licence) == (None, None)


class _Commits:
    """Counts commits of a batch transaction.

    Implements: Fake.
    """

    def __init__(self) -> None:
        self.count = 0

    async def commit(self) -> None:
        self.count += 1


def _scope(
    commits: _Commits,
    *,
    provenance: InMemoryProvenanceUnitOfWork,
    events: InMemoryEventsUnitOfWork,
    impacts: InMemoryImpactsUnitOfWork,
) -> SessionBatchScope:
    hazards = InMemoryHazardsUnitOfWork([HazardTypeTestFactory.build(code="glof")])
    geography = InMemoryGeographyUnitOfWork([PlaceTestFactory.build(code=HUNZA)])
    services = _services(
        _stores(events=events, provenance=provenance),
        core=_core(hazards=hazards, geography=geography),
        impacts=impacts,
    )
    return SessionBatchScope(
        commit=commits.commit,
        registrar=RegisterSourceHandler(
            InMemoryUnitOfWorkFactory(provenance), CLOCK, IDS
        ),
        create_event=CreateHistoricalEventHandler(services.event_handler_dependencies),
        record_claim=RecordImpactClaimHandler(
            services.impact_claim_handler_dependencies
        ),
    )


async def test_batch_scope_create_writes_source_event_and_claims() -> None:
    provenance = InMemoryProvenanceUnitOfWork()
    events = InMemoryEventsUnitOfWork()
    impacts = InMemoryImpactsUnitOfWork([COUNT_METRIC])
    lineage = await RegisterSourceHandler(
        InMemoryUnitOfWorkFactory(provenance), CLOCK, IDS
    )(
        RegisterSource(
            actor=MODERATOR,
            source_type=SourceType.DATASET,
            details=SourceDetails(title="Lineage", citation="Import job."),
        )
    )
    commits = _Commits()
    scope = _scope(commits, provenance=provenance, events=events, impacts=impacts)
    draft = _draft(claims=(_claim("deaths", CountValue(count=3)),))

    event_id = await scope.create(draft, lineage_source_id=lineage.id, actor=MODERATOR)
    await scope.commit()

    event = events.events.committed[event_id]
    (row_source_id,) = (
        source_id for source_id in event.source_ids if source_id != lineage.id
    )
    row_source = provenance.sources.committed[row_source_id]
    (claim,) = impacts.impact_claims.committed.values()
    assert event.status is EventStatus.DRAFT
    assert lineage.id in event.source_ids
    assert row_source.source_type is ROW_SOURCE_TYPE
    assert row_source.details == row_source_details(draft)
    assert (claim.event_id, claim.source_id) == (event_id, row_source_id)
    assert (commits.count, scope.is_committed) == (1, True)


async def test_batch_scope_after_commit_refuses_writes_and_second_commit() -> None:
    commits = _Commits()
    scope = _scope(
        commits,
        provenance=InMemoryProvenanceUnitOfWork(),
        events=InMemoryEventsUnitOfWork(),
        impacts=InMemoryImpactsUnitOfWork(),
    )
    await scope.commit()

    with pytest.raises(InvariantViolationError):
        await scope.commit()
    with pytest.raises(InvariantViolationError):
        await scope.create(_draft(), lineage_source_id=IDS.new_id(), actor=MODERATOR)

    assert commits.count == 1


# --------------------------------------------------------------------------- #
# Tasks                                                                       #
# --------------------------------------------------------------------------- #


def _exchange_handlers() -> tuple[RunExportHandler, RunImportHandler]:
    api = build_test_app()
    return api.exchange_services.run_export_handler, (
        api.exchange_services.run_import_handler
    )


async def test_run_export_task_adapter_validates_payload_and_runs_the_job() -> None:
    run_export, _ = _exchange_handlers()
    adapter = RunExportTaskAdapter(run_export)
    job_id = IDS.new_id()

    with pytest.raises(ExportJobNotFoundError):
        await adapter(_task("exchange.run_export", {"export_job_id": str(job_id)}))
    with pytest.raises(pydantic.ValidationError):
        await adapter(_task("exchange.run_export", {"job_id": str(job_id)}))


async def test_run_import_task_adapter_validates_payload_and_runs_the_job() -> None:
    _, run_import = _exchange_handlers()
    adapter = RunImportTaskAdapter(run_import)
    job_id = IDS.new_id()

    with pytest.raises(ImportJobNotFoundError):
        await adapter(_task("exchange.run_import", {"import_job_id": str(job_id)}))
    with pytest.raises(pydantic.ValidationError):
        await adapter(_task("exchange.run_import", {}))
