"""Unit tests for the platform source commands and their handlers (ADR 0020)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.provenance import (
    InMemoryProvenanceUnitOfWork,
    InMemorySourceRepository,
)
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.provenance.application.commands import (
    MarkPlatformSourceReferenced,
    RegisterPlatformSource,
)
from yakhnama.modules.provenance.application.handlers import (
    MarkPlatformSourceReferencedHandler,
    RegisterPlatformSourceHandler,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.modules.provenance.domain.events import (
    SourceReferenced,
    SourceRegistered,
)
from yakhnama.modules.provenance.domain.factories import SourceFactory
from yakhnama.modules.provenance.domain.value_objects import (
    SourceDetails,
    SourceOwner,
    SourceType,
)
from yakhnama.shared_kernel.errors import ConflictError, PermissionDeniedError

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
DETAILS = SourceDetails(
    title="Guest community report", citation="Yakhnama guest community report"
)
IDS = SequentialIdGenerator(seed=9)
RESERVED_ID = IDS.new_id()
USER_ID = IDS.new_id()


def _handlers(
    uow: InMemoryProvenanceUnitOfWork,
) -> tuple[RegisterPlatformSourceHandler, MarkPlatformSourceReferencedHandler]:
    factory = InMemoryUnitOfWorkFactory(uow)
    clock = FrozenClock(NOW)
    ids = SequentialIdGenerator(seed=10)
    return (
        RegisterPlatformSourceHandler(factory, clock, ids),
        MarkPlatformSourceReferencedHandler(factory, clock, ids),
    )


def _user_source() -> Source:
    return (
        SourceFactory()
        .register(
            SourceType.CITIZEN,
            DETAILS,
            SourceOwner(actor_id=USER_ID),
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(seed=11),
            source_id=RESERVED_ID,
        )
        .state
    )


async def test_register_platform_source_stores_ownerless_unreferenced_source() -> None:
    uow = InMemoryProvenanceUnitOfWork()
    register, _ = _handlers(uow)

    detail = await register(
        RegisterPlatformSource(source_type=SourceType.CITIZEN, details=DETAILS)
    )

    stored = uow.sources.committed[detail.id]
    assert stored.owner_actor_id is None
    assert stored.organization_id is None
    assert stored.is_referenced is False
    assert stored.title == DETAILS.title
    assert [type(event) for event in uow.committed_events] == [SourceRegistered]


async def test_register_platform_source_with_reserved_id_is_idempotent() -> None:
    uow = InMemoryProvenanceUnitOfWork()
    register, _ = _handlers(uow)
    command = RegisterPlatformSource(
        source_type=SourceType.CITIZEN, details=DETAILS, source_id=RESERVED_ID
    )

    first = await register(command)
    again = await register(command)

    assert first.id == again.id == RESERVED_ID
    assert list(uow.sources.committed) == [RESERVED_ID]
    assert len(uow.committed_events) == 1


async def test_register_platform_source_reserved_id_of_a_user_source_conflicts() -> (
    None
):
    uow = InMemoryProvenanceUnitOfWork(sources=[_user_source()])
    register, _ = _handlers(uow)

    with pytest.raises(ConflictError):
        await register(
            RegisterPlatformSource(
                source_type=SourceType.CITIZEN, details=DETAILS, source_id=RESERVED_ID
            )
        )


class RacingProvenanceUnitOfWork(InMemoryProvenanceUnitOfWork):
    """Lets a concurrent request register the reserved source first.

    Implements: Fake (of Unit of Work).
    """

    def __init__(self, racer: Source | None) -> None:
        """Create the unit of work; ``racer`` appears on the first commit."""
        super().__init__()
        self.racer = racer

    def _on_begin(self) -> None:
        # The second unit of work (the insert) sees the racer's committed row.
        if self.racer is not None and self.commit_count == 0 and self.rollback_count:
            self.sources.committed[self.racer.id] = self.racer
            self.racer = None


async def test_register_platform_source_losing_the_insert_race_returns_winner() -> None:
    winner = (
        SourceFactory()
        .register(
            SourceType.CITIZEN,
            DETAILS,
            SourceOwner(),
            clock=FrozenClock(NOW),
            ids=SequentialIdGenerator(seed=12),
            source_id=RESERVED_ID,
        )
        .state
    )
    uow = RacingProvenanceUnitOfWork(winner)
    register, _ = _handlers(uow)

    detail = await register(
        RegisterPlatformSource(
            source_type=SourceType.CITIZEN, details=DETAILS, source_id=RESERVED_ID
        )
    )

    assert detail.id == RESERVED_ID
    assert detail.created_at == winner.created_at
    assert list(uow.sources.committed) == [RESERVED_ID]


async def test_register_platform_source_conflict_without_reserved_id_propagates() -> (
    None
):
    uow = InMemoryProvenanceUnitOfWork()
    register = RegisterPlatformSourceHandler(
        InMemoryUnitOfWorkFactory(uow), FrozenClock(NOW), SequentialIdGenerator(seed=9)
    )
    uow.sources.committed[RESERVED_ID] = _user_source()

    with pytest.raises(ConflictError):
        await register(
            RegisterPlatformSource(source_type=SourceType.CITIZEN, details=DETAILS)
        )


async def test_mark_platform_source_referenced_freezes_it_once() -> None:
    uow = InMemoryProvenanceUnitOfWork()
    register, mark = _handlers(uow)
    detail = await register(
        RegisterPlatformSource(source_type=SourceType.CITIZEN, details=DETAILS)
    )

    marked = await mark(MarkPlatformSourceReferenced(source_id=detail.id))
    again = await mark(MarkPlatformSourceReferenced(source_id=detail.id))

    assert marked.is_referenced is True
    assert again == marked
    assert [type(event) for event in uow.committed_events] == [
        SourceRegistered,
        SourceReferenced,
    ]


async def test_mark_platform_source_referenced_refuses_a_user_source() -> None:
    uow = InMemoryProvenanceUnitOfWork(sources=[_user_source()])
    _, mark = _handlers(uow)

    with pytest.raises(PermissionDeniedError):
        await mark(MarkPlatformSourceReferenced(source_id=RESERVED_ID))


async def test_mark_platform_source_referenced_unknown_source_is_not_found() -> None:
    _, mark = _handlers(InMemoryProvenanceUnitOfWork())

    with pytest.raises(SourceNotFoundError):
        await mark(MarkPlatformSourceReferenced(source_id=RESERVED_ID))


@pytest.mark.parametrize(
    "source_type",
    [
        SourceType.GOVERNMENT,
        SourceType.NEWS,
        SourceType.SATELLITE,
        SourceType.RESEARCH,
        SourceType.DATASET,
    ],
)
def test_register_platform_source_ranked_type_is_refused(
    source_type: SourceType,
) -> None:
    with pytest.raises(ValidationError, match="citizen or organisation"):
        RegisterPlatformSource(source_type=source_type, details=DETAILS)


class RacingSourceRepository(InMemorySourceRepository):
    """Lets a concurrent request commit a change just before the first save.

    Implements: Fake (of Repository).
    """

    def __init__(self, *, marks: bool) -> None:
        """Create the repository; ``marks`` says whether the race marks it."""
        super().__init__()
        self.marks = marks
        self.raced = False

    async def save(self, source: Source) -> None:
        """Commit the concurrent change once, then save as the real one would."""
        if not self.raced:
            self.raced = True
            stored = self.committed[source.id]
            clock, ids = FrozenClock(NOW), SequentialIdGenerator(seed=13)
            self.committed[source.id] = (
                stored.mark_referenced(clock=clock, ids=ids).state
                if self.marks
                else stored.update_details(
                    SourceDetails(title="Changed", citation="Changed"),
                    clock=clock,
                    ids=ids,
                ).state
            )
        await super().save(source)


def racing_uow(*, marks: bool) -> InMemoryProvenanceUnitOfWork:
    """Return a provenance unit of work whose first save loses a race."""
    uow = InMemoryProvenanceUnitOfWork()
    uow.sources = RacingSourceRepository(marks=marks)
    return uow


async def test_mark_platform_source_losing_to_the_same_mark_returns_it() -> None:
    uow = racing_uow(marks=True)
    register, mark = _handlers(uow)
    detail = await register(
        RegisterPlatformSource(source_type=SourceType.CITIZEN, details=DETAILS)
    )

    marked = await mark(MarkPlatformSourceReferenced(source_id=detail.id))

    assert marked.is_referenced is True


async def test_mark_platform_source_losing_to_another_change_conflicts() -> None:
    uow = racing_uow(marks=False)
    register, mark = _handlers(uow)
    detail = await register(
        RegisterPlatformSource(source_type=SourceType.CITIZEN, details=DETAILS)
    )

    with pytest.raises(ConflictError):
        await mark(MarkPlatformSourceReferenced(source_id=detail.id))
