"""Unit tests for ``yakhnama.platform.wiring.impacts`` over the events fakes."""

from datetime import UTC, datetime
from typing import Final, get_args

import pytest

from tests.factories.events import EventTestFactory
from tests.factories.provenance import SourceTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.events import InMemoryEventQueryService, InMemoryEventsUnitOfWork
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import InMemoryProvenanceUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.events.public import EventStatus
from yakhnama.modules.identity.public import Role
from yakhnama.modules.impacts.public import SourceTypeName
from yakhnama.modules.provenance.public import (
    MarkSourceReferencedHandler,
    SourceNotFoundError,
    SourceType,
)
from yakhnama.modules.verification.public import VerificationState
from yakhnama.platform.wiring.impacts import (
    SOURCE_TYPE_NAMES,
    HazardEventDirectoryAdapter,
    ImpactSourceMarkerAdapter,
)

IDS: Final = SequentialIdGenerator(seed=601)
CLOCK: Final = FrozenClock(datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
MODERATOR: Final = actor_with({Role.MODERATOR}, user_id=IDS.new_id())
VERIFIED: Final = VerificationState.VERIFIED.value


async def test_hazard_event_directory_adapter_exists_for_any_status() -> None:
    event = EventTestFactory.build(status=EventStatus.DRAFT)
    adapter = HazardEventDirectoryAdapter(
        InMemoryEventQueryService(InMemoryEventsUnitOfWork([event]))
    )

    known = await adapter.exists(event.id)
    unknown = await adapter.exists(IDS.new_id())

    assert (known, unknown) == (True, False)


@pytest.mark.parametrize(
    ("status", "state", "expected"),
    [
        (EventStatus.PUBLISHED, VERIFIED, True),
        (EventStatus.PUBLISHED, VerificationState.UNDER_REVIEW.value, False),
        (EventStatus.PUBLISHED, None, False),
        (EventStatus.DRAFT, VERIFIED, False),
    ],
)
async def test_hazard_event_directory_adapter_visible_only_if_published_and_verified(
    status: EventStatus, state: str | None, *, expected: bool
) -> None:
    event = EventTestFactory.build(status=status)
    reads = InMemoryEventQueryService(
        InMemoryEventsUnitOfWork([event]),
        {} if state is None else {event.id: state},
    )
    adapter = HazardEventDirectoryAdapter(reads)

    visible = await adapter.is_publicly_visible(event.id)

    assert visible is expected


async def test_hazard_event_directory_adapter_missing_event_is_not_visible() -> None:
    adapter = HazardEventDirectoryAdapter(
        InMemoryEventQueryService(InMemoryEventsUnitOfWork())
    )

    visible = await adapter.is_publicly_visible(IDS.new_id())

    assert visible is False


def _marker(provenance: InMemoryProvenanceUnitOfWork) -> ImpactSourceMarkerAdapter:
    return ImpactSourceMarkerAdapter(
        MarkSourceReferencedHandler(InMemoryUnitOfWorkFactory(provenance), CLOCK, IDS)
    )


async def test_impact_source_marker_adapter_marks_and_returns_the_type_name() -> None:
    source = SourceTestFactory.build(source_type=SourceType.GOVERNMENT)
    provenance = InMemoryProvenanceUnitOfWork(sources=[source])

    name = await _marker(provenance).mark_referenced(source.id, actor=MODERATOR)

    assert name == "government"
    assert provenance.sources.committed[source.id].is_referenced is True


async def test_impact_source_marker_adapter_unknown_source_raises_not_found() -> None:
    adapter = _marker(InMemoryProvenanceUnitOfWork())

    with pytest.raises(SourceNotFoundError):
        await adapter.mark_referenced(IDS.new_id(), actor=MODERATOR)


def test_source_type_names_cover_every_provenance_type_with_a_ranked_name() -> None:
    assert set(SOURCE_TYPE_NAMES) == set(SourceType)
    assert set(SOURCE_TYPE_NAMES.values()) == set(get_args(SourceTypeName))
    assert all(
        name == source_type.value for source_type, name in SOURCE_TYPE_NAMES.items()
    )
