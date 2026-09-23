"""A fully faked impacts claims side for the application tests.

Metric codes are synthetic; nothing here asserts a real registry entry.

Patterns: Fake.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from tests.fakes.clock import FrozenClock, SteppingClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.impacts import (
    FakeHazardEventDirectory,
    InMemoryImpactQueryService,
    InMemoryImpactsUnitOfWork,
    RecordingImpactSourceMarker,
)
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.impacts.application.support import entry
from yakhnama.modules.identity.public import AuthorisationPolicy, Role
from yakhnama.modules.impacts.application.authorisation import moderation_policy
from yakhnama.modules.impacts.application.claims_handlers import (
    ImpactClaimHandlerDependencies,
)
from yakhnama.modules.impacts.application.claims_query_services import (
    EventImpactsQueryService,
)
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceEntry
from yakhnama.modules.impacts.domain.value_objects import (
    RetirementReason,
    SourceTypeName,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

NOW: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
_IDS: Final = SequentialIdGenerator(seed=717)
MODERATOR_ID: Final = _IDS.new_id()
MODERATOR = actor_with({Role.MODERATOR}, user_id=MODERATOR_ID)
CITIZEN = actor_with(user_id=_IDS.new_id())
SUM_METRIC: Final = "test_people_affected"
MAX_METRIC: Final = "test_houses_destroyed"
RETIRED_METRIC: Final = "test_old_metric"
REASON: Final = "The bulletin was revised."


def new_id() -> EntityId:
    """Return a fresh, deterministic UUIDv7."""
    return _IDS.new_id()


def day(number: int, month: int = 8) -> DateWithPrecision:
    """Return midnight UTC of a day in 2026 at ``day`` precision."""
    return DateWithPrecision(
        value=datetime(2026, month, number, tzinfo=UTC), precision=DatePrecision.DAY
    )


def metric(
    code: str, *, retirement: RetirementReason | None = None, aggregation: str = "sum"
) -> ImpactMetric:
    """Return a stored count metric with a synthetic code."""
    factory = ImpactMetricFactory(
        clock=FrozenClock(NOW - timedelta(days=30)),
        id_generator=SequentialIdGenerator(seed=12),
    )
    reference = ImpactMetricReferenceEntry.model_validate(
        entry(code, retirement=retirement, aggregation=aggregation)
    )
    return factory.create_from_reference(reference).state


class ImpactsWorld:
    """Every fake the claims handlers and read service need, wired together.

    Implements: Fake.

    Attributes:
        event_id: A hazard event that exists and is publicly visible.
        hidden_event_id: A hazard event that exists but is not public.
        government: A government source.
        citizen_source: A citizen source.
        uow: The unit of work every handler opens.
        sources: Records sources marked referenced.
        deps: The handler dependencies.
        service: The guarded read service.
    """

    def __init__(self, policy: AuthorisationPolicy | None = None) -> None:
        """Build the world with three metrics and two sources.

        Args:
            policy: The moderation policy; ``CanModerate`` by default.
        """
        chosen = policy or moderation_policy()
        self.event_id = new_id()
        self.hidden_event_id = new_id()
        self.government = new_id()
        self.citizen_source = new_id()
        types: dict[EntityId, SourceTypeName] = {
            self.government: "government",
            self.citizen_source: "citizen",
        }
        self.uow = InMemoryImpactsUnitOfWork(
            (
                metric(SUM_METRIC),
                metric(MAX_METRIC, aggregation="max"),
                metric(
                    RETIRED_METRIC,
                    retirement=RetirementReason(explanation="Replaced."),
                ),
            )
        )
        self.sources = RecordingImpactSourceMarker(types)
        events = FakeHazardEventDirectory(
            existing=(self.hidden_event_id,), public=(self.event_id,)
        )
        clock = SteppingClock(NOW, timedelta(seconds=1))
        self.deps = ImpactClaimHandlerDependencies(
            uow_factory=InMemoryUnitOfWorkFactory(self.uow),
            policy=chosen,
            clock=clock,
            ids=SequentialIdGenerator(seed=99),
            events=events,
            sources=self.sources,
        )
        self.service = EventImpactsQueryService(
            uow_factory=InMemoryUnitOfWorkFactory(self.uow),
            reads=InMemoryImpactQueryService(self.uow),
            events=events,
            policy=chosen,
            clock=FrozenClock(NOW),
        )
