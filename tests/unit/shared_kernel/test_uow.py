"""Unit tests for ``yakhnama.shared_kernel.uow``.

The protocols have no behaviour of their own; these tests pin the contract a unit of
work must satisfy by driving the shared in-memory fake (``tests.fakes.uow``) through
it, and mypy checks that the fake conforms structurally.
"""

from datetime import UTC, datetime
from typing import ClassVar, get_protocol_members

import pytest

from tests.fakes.uow import InMemoryUnitOfWork, InMemoryUnitOfWorkFactory
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

ids = Uuid7Generator()


class _SomethingHappened(DomainEvent):
    """Example event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "tests.something_happened"


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
    factory: UnitOfWorkFactory[InMemoryUnitOfWork] = InMemoryUnitOfWorkFactory(
        InMemoryUnitOfWork()
    )
    event = _event()

    async with factory() as uow:
        uow.record_event(event)
        collected = list(uow.collected_events)
        await uow.commit()

    assert collected == [event]
    assert factory().committed_events == (event,)
    assert factory().collected_events == ()


async def test_unit_of_work_exception_inside_block_discards_events() -> None:
    uow: UnitOfWork = InMemoryUnitOfWork()

    with pytest.raises(_UseCaseFailedError):
        await _fail_after_recording(uow)

    assert uow.collected_events == ()


async def test_unit_of_work_aggregate_change_record_into_collects_events() -> None:
    uow = InMemoryUnitOfWork()
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
