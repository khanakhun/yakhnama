"""Unit tests for ``yakhnama.platform.wiring.events`` over the other modules' fakes."""

from datetime import UTC, datetime, timedelta
from typing import Final

from tests.factories.geography import PlaceTestFactory
from tests.factories.impacts import ImpactClaimTestFactory
from tests.factories.provenance import SourceTestFactory
from tests.factories.reports import ReportTestFactory
from tests.factories.verification import VerificationCaseTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.geography import InMemoryGeographyUnitOfWork
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import InMemoryImpactQueryService, InMemoryImpactsUnitOfWork
from tests.fakes.provenance import InMemoryProvenanceUnitOfWork
from tests.fakes.reports import InMemoryReportQueryService, InMemoryReportsUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.fakes.verification import (
    FakeReportOwnerLookup,
    FakeReviewerEligibility,
    InMemoryVerificationQueryService,
    InMemoryVerificationUnitOfWork,
)
from yakhnama.modules.events.public import ReportForEvent, TimelineEntryKind
from yakhnama.modules.identity.public import Role
from yakhnama.modules.provenance.public import MarkSourceReferencedHandler
from yakhnama.modules.reports.public import ObservationPoint
from yakhnama.modules.verification.public import (
    OpenVerificationCaseHandler,
    TargetKind,
    Transition,
    VerificationHandlerDependencies,
    VerificationState,
    VerificationTarget,
    moderation_policy,
)
from yakhnama.platform.wiring.events import (
    EventSourceMarkerAdapter,
    EventTimelineAdapter,
    PlaceDirectoryAdapter,
    ReportFactsAdapter,
    VerificationCaseOpenerAdapter,
)
from yakhnama.shared_kernel.pagination import MAX_PAGE_LIMIT
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

IDS: Final = SequentialIdGenerator(seed=501)
NOW: Final = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
CLOCK: Final = FrozenClock(NOW)
MODERATOR: Final = actor_with({Role.MODERATOR}, user_id=IDS.new_id())
TWO_DECIMALS: Final = PublicCoordinatePolicy(decimals=2)
EXACT: Final = Coordinates(longitude=74.654321, latitude=36.314159)


async def test_report_facts_adapter_rounds_each_point_and_skips_missing_ids() -> None:
    report = ReportTestFactory.build(observation=ObservationPoint(coordinates=EXACT))
    reports = InMemoryReportQueryService(InMemoryReportsUnitOfWork(reports=[report]))
    adapter = ReportFactsAdapter(reports, TWO_DECIMALS)

    facts = await adapter.get_many([IDS.new_id(), report.id, report.id])

    assert facts == [
        ReportForEvent(
            id=report.id,
            observed_at=report.observed_at,
            coordinates=Coordinates(longitude=74.65, latitude=36.31),
            source_id=report.source_id,
        )
    ]


async def test_event_source_marker_adapter_marks_each_source_once() -> None:
    first, second = SourceTestFactory.build(), SourceTestFactory.build()
    provenance = InMemoryProvenanceUnitOfWork(sources=[first, second])
    marker = MarkSourceReferencedHandler(
        InMemoryUnitOfWorkFactory(provenance), CLOCK, IDS
    )
    adapter = EventSourceMarkerAdapter(marker)

    await adapter.mark_referenced([first.id, second.id, first.id], actor=MODERATOR)

    committed = provenance.sources.committed
    assert committed[first.id].is_referenced is True
    assert committed[second.id].is_referenced is True
    assert committed[first.id].version == first.version + 1


async def test_verification_case_opener_adapter_opens_one_case_per_event() -> None:
    verification = InMemoryVerificationUnitOfWork()
    handler = OpenVerificationCaseHandler(
        VerificationHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(verification),
            policy=moderation_policy(),
            clock=CLOCK,
            ids=IDS,
            report_owners=FakeReportOwnerLookup(),
            reviewers=FakeReviewerEligibility(),
        )
    )
    adapter = VerificationCaseOpenerAdapter(handler)
    event_id = IDS.new_id()

    await adapter.open_for_event(event_id, actor=MODERATOR)
    await adapter.open_for_event(event_id, actor=MODERATOR)

    cases = list(verification.verification_cases.committed.values())
    assert [case.target for case in cases] == [
        VerificationTarget(kind=TargetKind.EVENT, target_id=event_id)
    ]


async def test_place_directory_adapter_tells_known_and_unknown_codes() -> None:
    place = PlaceTestFactory.build(code="test.place.wiring")
    adapter = PlaceDirectoryAdapter(
        InMemoryUnitOfWorkFactory(InMemoryGeographyUnitOfWork([place]))
    )

    known = await adapter.exists("test.place.wiring")
    unknown = await adapter.exists("test.place.absent")

    assert (known, unknown) == (True, False)


def _timeline(
    verification: InMemoryVerificationUnitOfWork, impacts: InMemoryImpactsUnitOfWork
) -> EventTimelineAdapter:
    return EventTimelineAdapter(
        InMemoryVerificationQueryService(verification.verification_cases),
        InMemoryImpactQueryService(impacts),
    )


def _transition(to_state: VerificationState, at: datetime) -> Transition:
    return Transition(
        from_state=VerificationState.SUBMITTED,
        to_state=to_state,
        actor_id=MODERATOR.user_id or IDS.new_id(),
        reason="Checked against the field team.",
        occurred_at=at,
        is_human=True,
    )


async def test_event_timeline_adapter_lists_each_transition_at_exact_time() -> None:
    event_id = IDS.new_id()
    case = VerificationCaseTestFactory.build(
        target=VerificationTarget(kind=TargetKind.EVENT, target_id=event_id),
        state=VerificationState.UNDER_REVIEW,
        history=(_transition(VerificationState.UNDER_REVIEW, NOW),),
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
    )
    adapter = _timeline(
        InMemoryVerificationUnitOfWork([case]), InMemoryImpactsUnitOfWork()
    )

    entries = await adapter.verification_transitions(event_id)

    assert [(entry.kind, entry.subject_id, entry.label) for entry in entries] == [
        (TimelineEntryKind.VERIFICATION_TRANSITION, case.id, "under_review")
    ]
    assert entries[0].at == DateWithPrecision(value=NOW, precision=DatePrecision.EXACT)


async def test_event_timeline_adapter_event_without_case_has_no_transitions() -> None:
    adapter = _timeline(InMemoryVerificationUnitOfWork(), InMemoryImpactsUnitOfWork())

    entries = await adapter.verification_transitions(IDS.new_id())

    assert tuple(entries) == ()


async def test_event_timeline_adapter_lists_every_claim_across_pages() -> None:
    event_id = IDS.new_id()
    claims = [
        ImpactClaimTestFactory.build(
            event_id=event_id, created_at=NOW + timedelta(seconds=index)
        )
        for index in range(MAX_PAGE_LIMIT + 1)
    ]
    other = ImpactClaimTestFactory.build()
    impacts = InMemoryImpactsUnitOfWork(claims=[*claims, other])
    adapter = _timeline(InMemoryVerificationUnitOfWork(), impacts)

    entries = await adapter.impact_claims(event_id)

    assert [entry.subject_id for entry in entries] == [claim.id for claim in claims]
    assert {entry.kind for entry in entries} == {TimelineEntryKind.IMPACT_CLAIM}
    assert entries[0].label == claims[0].metric.code
    assert entries[0].at == claims[0].claimed_at
