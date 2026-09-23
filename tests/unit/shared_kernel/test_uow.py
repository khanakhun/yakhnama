"""Unit tests for ``yakhnama.shared_kernel.uow``.

The protocols have no behaviour of their own; these tests pin the contract a unit of
work must satisfy by driving a minimal in-memory implementation through it, and mypy
checks that the implementation conforms structurally.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import ClassVar, Self, get_protocol_members

import pytest

from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

ids = Uuid7Generator()


class _SomethingHappened(DomainEvent):
    """Example event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "tests.something_happened"


class _InMemoryUnitOfWork:
    """Minimal unit of work honouring the protocol's documented semantics.

    Implements: Fake.
    """

    def __init__(self) -> None:
        self.published: list[DomainEvent] = []
        self._pending: list[DomainEvent] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.rollback()

    async def commit(self) -> None:
        self.published.extend(self._pending)
        self._pending.clear()

    async def rollback(self) -> None:
        self._pending.clear()

    def record_event(self, event: DomainEvent) -> None:
        self._pending.append(event)

    @property
    def collected_events(self) -> Sequence[DomainEvent]:
        return tuple(self._pending)


class _InMemoryUnitOfWorkFactory:
    """Returns one shared in-memory unit of work.

    Implements: Fake.
    """

    def __init__(self) -> None:
        self.uow = _InMemoryUnitOfWork()

    def __call__(self) -> _InMemoryUnitOfWork:
        return self.uow


def _event() -> _SomethingHappened:
    return _SomethingHappened(
        event_id=ids.new_id(),
        occurred_at=datetime(2026, 9, 23, tzinfo=UTC),
        aggregate_id=ids.new_id(),
        aggregate_type="thing",
    )


class _UseCaseFailedError(Exception):
    """Failure raised inside a use case.

    Implements: Domain Error.
    """


async def _fail_after_recording(uow: UnitOfWork) -> None:
    message = "use case failed"
    async with uow:
        uow.record_event(_event())
        raise _UseCaseFailedError(message)


async def test_unit_of_work_commit_publishes_collected_events() -> None:
    factory: UnitOfWorkFactory[_InMemoryUnitOfWork] = _InMemoryUnitOfWorkFactory()
    event = _event()

    async with factory() as uow:
        uow.record_event(event)
        collected = list(uow.collected_events)
        await uow.commit()

    assert collected == [event]
    assert factory().published == [event]
    assert factory().collected_events == ()


async def test_unit_of_work_exception_inside_block_discards_events() -> None:
    uow: UnitOfWork = _InMemoryUnitOfWork()

    with pytest.raises(_UseCaseFailedError):
        await _fail_after_recording(uow)

    assert uow.collected_events == ()


async def test_unit_of_work_aggregate_change_record_into_collects_events() -> None:
    uow = _InMemoryUnitOfWork()
    event = _event()
    change = AggregateChange[_SomethingHappened](state=event, events=(event,))

    async with uow:
        state = change.record_into(uow)
        collected = uow.collected_events

    assert collected == (event,)
    assert state is event


def test_unit_of_work_protocol_declares_the_documented_members() -> None:
    expected = {
        "__aenter__",
        "__aexit__",
        "commit",
        "rollback",
        "record_event",
        "collected_events",
    }

    members = get_protocol_members(UnitOfWork)

    assert members == expected
    assert get_protocol_members(UnitOfWorkFactory) == {"__call__"}
