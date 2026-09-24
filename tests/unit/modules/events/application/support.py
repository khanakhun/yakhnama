"""A fully faked events module for the application tests.

Patterns: Fake.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from tests.factories.events import EventTestFactory
from tests.factories.hazards import HazardTypeTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.events import (
    FakeEventTimelineSources,
    FakePlaceDirectory,
    FakeReportFactsProvider,
    InMemoryEventQueryService,
    InMemoryEventsUnitOfWork,
    RecordingSourceReferenceMarker,
    RecordingVerificationCaseOpener,
)
from tests.fakes.hazards import (
    InMemoryHazardTypeQueryService,
    InMemoryHazardTypeRepository,
)
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.events.application.authorisation import moderation_policy
from yakhnama.modules.events.application.dto import TimelineEntry
from yakhnama.modules.events.application.handlers import EventHandlerDependencies
from yakhnama.modules.events.application.query_services import (
    EventRecordQueryService,
)
from yakhnama.modules.events.domain.entities import Event
from yakhnama.modules.events.domain.value_objects import (
    EventStatus,
    ReportForEvent,
    ReportLink,
)
from yakhnama.modules.hazards.domain.value_objects import RetirementReason
from yakhnama.modules.identity.public import AuthorisationPolicy, Role
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

NOW: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
_IDS: Final = SequentialIdGenerator(seed=616)
MODERATOR_ID: Final = _IDS.new_id()
MODERATOR = actor_with({Role.MODERATOR}, user_id=MODERATOR_ID)
CITIZEN = actor_with(user_id=_IDS.new_id())
KNOWN_PLACE: Final = "test.place-known"
RETIRED_HAZARD: Final = "test_hazard_retired"
REASON: Final = "Recorded twice from the same bulletin."
DECIMALS: Final = 2


def new_id() -> EntityId:
    """Return a fresh, deterministic UUIDv7."""
    return _IDS.new_id()


def day(year: int, month: int, number: int) -> DateWithPrecision:
    """Return midnight UTC of a day at ``day`` precision."""
    return DateWithPrecision(
        value=datetime(year, month, number, tzinfo=UTC), precision=DatePrecision.DAY
    )


def report(
    longitude: float = 74.123456,
    latitude: float = 36.987654,
    observed_at: DateWithPrecision | None = None,
) -> ReportForEvent:
    """Return a report view with an exact, unrounded point and a fresh source."""
    return ReportForEvent(
        id=new_id(),
        observed_at=observed_at or day(2026, 8, 10),
        coordinates=Coordinates(longitude=longitude, latitude=latitude),
        source_id=new_id(),
    )


def stored_event(
    *,
    status: EventStatus = EventStatus.DRAFT,
    reports: tuple[ReportForEvent, ...] = (),
    **fields: object,
) -> Event:
    """Return a ``glof`` event at version 1 linked to ``reports`` as primary."""
    links = tuple(
        ReportLink(
            report_id=item.id,
            linked_by=MODERATOR_ID,
            linked_at=NOW - timedelta(days=1),
            role="primary",
        )
        for item in reports
    )
    base = EventTestFactory.build(
        created_at=NOW - timedelta(days=2), updated_at=NOW - timedelta(days=2)
    )
    changes: dict[str, object] = {"status": status, "report_links": links}
    if status is EventStatus.RETRACTED:
        changes["status_reason"] = REASON
    return Event.model_validate({**dict(base), **changes, **fields})


class EventsWorld:
    """Every fake the events handlers and read service need, wired together.

    Implements: Fake.

    Attributes:
        uow: The unit of work every handler opens.
        reports: The report facts the handlers see.
        sources: Records sources marked referenced.
        cases: Records verification cases opened.
        reads: The events read port over ``uow``.
        timeline: Fixed verification and claim entries.
        deps: The handler dependencies.
        service: The guarded read service.
    """

    def __init__(
        self,
        *events: Event,
        reports: tuple[ReportForEvent, ...] = (),
        policy: AuthorisationPolicy | None = None,
        transitions: dict[EntityId, list[TimelineEntry]] | None = None,
        claims: dict[EntityId, list[TimelineEntry]] | None = None,
    ) -> None:
        """Build the world.

        Args:
            events: Events stored before the test acts.
            reports: Reports that exist.
            policy: The moderation policy; ``CanModerate`` by default.
            transitions: Verification timeline entries per event.
            claims: Claim timeline entries per event.
        """
        chosen = policy or moderation_policy()
        self.uow = InMemoryEventsUnitOfWork(events)
        self.reports = FakeReportFactsProvider(reports)
        self.sources = RecordingSourceReferenceMarker()
        self.cases = RecordingVerificationCaseOpener()
        self.reads = InMemoryEventQueryService(self.uow)
        self.timeline = FakeEventTimelineSources(transitions, claims)
        retired = HazardTypeTestFactory.build(code=RETIRED_HAZARD)
        retired = retired.retire(
            RetirementReason(text="Obsolete test type."),
            clock=SteppingClock(NOW, timedelta(seconds=1)),
            ids=SequentialIdGenerator(seed=5),
        ).state
        hazard_types = InMemoryHazardTypeQueryService(
            InMemoryHazardTypeRepository(
                (HazardTypeTestFactory.build(code="glof"), retired)
            )
        )
        self.deps = EventHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(self.uow),
            policy=chosen,
            clock=SteppingClock(NOW, timedelta(seconds=1)),
            ids=SequentialIdGenerator(seed=88),
            reports=self.reports,
            sources=self.sources,
            cases=self.cases,
            places=FakePlaceDirectory({KNOWN_PLACE}),
            hazard_types=hazard_types,
            coordinates=PublicCoordinatePolicy(decimals=DECIMALS),
        )
        self.service = EventRecordQueryService(
            self.reads,
            policy=chosen,
            reports=self.reports,
            timeline_sources=self.timeline,
        )

    def stored(self, event_id: EntityId) -> Event:
        """Return the committed state of an event."""
        return self.uow.events.committed[event_id]
