"""Unit tests for ``tests.fakes.uow``.

These pin the rules the fake shares with ``yakhnama.platform.uow``, so a handler that
passes against the fake behaves the same against the database.
"""

from datetime import UTC, datetime
from typing import ClassVar, get_protocol_members

import pytest

from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWork, InMemoryUnitOfWorkFactory
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory

ids = SequentialIdGenerator()


class _ThingHappened(DomainEvent):
    """Example event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "tests.thing_happened"


class _UseCaseFailedError(Exception):
    """Failure raised inside a use case.

    Implements: Domain Error.
    """


class _StagingUnitOfWork(InMemoryUnitOfWork):
    """Subclass recording its hooks, as a module's fake unit of work would.

    Implements: Fake.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hooks: list[str] = []

    def _on_begin(self) -> None:
        self.hooks.append("begin")

    def _on_commit(self) -> None:
        self.hooks.append("commit")

    def _on_rollback(self) -> None:
        self.hooks.append("rollback")


def _event() -> _ThingHappened:
    return _ThingHappened(
        event_id=ids.new_id(),
        occurred_at=datetime(2026, 9, 23, tzinfo=UTC),
        aggregate_id=ids.new_id(),
        aggregate_type="thing",
    )


def test_in_memory_unit_of_work_declares_every_protocol_member() -> None:
    members = get_protocol_members(UnitOfWork)

    missing = {m for m in members if not hasattr(InMemoryUnitOfWork, m)}

    assert missing == set()


async def test_in_memory_unit_of_work_commit_moves_events_to_committed_events() -> None:
    uow: UnitOfWork = InMemoryUnitOfWork()
    event = _event()

    async with uow:
        uow.record_event(event)
        collected = tuple(uow.collected_events)
        await uow.commit()

    assert isinstance(uow, InMemoryUnitOfWork)
    assert collected == (event,)
    assert uow.collected_events == ()
    assert uow.committed_events == (event,)
    assert (uow.committed, uow.rolled_back, uow.commit_count) == (True, False, 1)


async def test_in_memory_unit_of_work_exit_without_commit_rolls_back() -> None:
    uow = InMemoryUnitOfWork()

    async with uow:
        uow.record_event(_event())

    assert uow.collected_events == ()
    assert uow.committed_events == ()
    assert (uow.committed, uow.rolled_back, uow.rollback_count) == (False, True, 1)
    assert not uow.is_active


async def _fail_after_recording(uow: UnitOfWork) -> None:
    message = "use case failed"
    async with uow:
        uow.record_event(_event())
        raise _UseCaseFailedError(message)


async def test_in_memory_unit_of_work_exception_rolls_back_and_propagates() -> None:
    uow = InMemoryUnitOfWork()

    with pytest.raises(_UseCaseFailedError):
        await _fail_after_recording(uow)

    assert uow.rolled_back
    assert uow.collected_events == ()
    assert uow.committed_events == ()


async def test_in_memory_unit_of_work_double_commit_raises_invariant_violation() -> (
    None
):
    uow = InMemoryUnitOfWork()

    async with uow:
        await uow.commit()
        with pytest.raises(InvariantViolationError, match="already committed"):
            await uow.commit()

    assert uow.commit_count == 1


async def test_in_memory_unit_of_work_record_after_commit_raises() -> None:
    uow = InMemoryUnitOfWork()

    async with uow:
        await uow.commit()
        with pytest.raises(InvariantViolationError, match="after the unit of work"):
            uow.record_event(_event())


@pytest.mark.parametrize("action", ["commit", "rollback"])
async def test_in_memory_unit_of_work_outside_block_raises(action: str) -> None:
    uow = InMemoryUnitOfWork()
    operation = uow.commit if action == "commit" else uow.rollback

    with pytest.raises(InvariantViolationError, match="not active"):
        await operation()


async def test_in_memory_unit_of_work_nested_entry_raises() -> None:
    uow = InMemoryUnitOfWork()

    async with uow:
        with pytest.raises(InvariantViolationError, match="already active"):
            await uow.__aenter__()


async def test_in_memory_unit_of_work_reentry_starts_a_new_transaction() -> None:
    uow = InMemoryUnitOfWork()
    first, second = _event(), _event()

    async with uow:
        uow.record_event(first)
        await uow.commit()
    async with uow:
        is_reset = (uow.committed, uow.rolled_back) == (False, False)
        uow.record_event(second)
        await uow.commit()

    assert is_reset
    assert uow.committed_events == (first, second)
    assert uow.commit_count == 2


async def test_in_memory_unit_of_work_hooks_run_in_transaction_order() -> None:
    uow = _StagingUnitOfWork()

    async with uow:
        await uow.commit()
    async with uow:
        pass

    assert uow.hooks == ["begin", "commit", "begin", "rollback"]


async def test_in_memory_unit_of_work_factory_returns_the_shared_instance() -> None:
    uow = InMemoryUnitOfWork()
    factory: UnitOfWorkFactory[InMemoryUnitOfWork] = InMemoryUnitOfWorkFactory(uow)
    event = _event()

    async with factory() as opened:
        opened.record_event(event)
        await opened.commit()

    assert factory() is uow
    assert uow.committed_events == (event,)
    assert isinstance(factory, InMemoryUnitOfWorkFactory)
    assert factory.calls == 2


def test_in_memory_unit_of_work_factory_keeps_the_subclass_type() -> None:
    uow = _StagingUnitOfWork()
    factory = InMemoryUnitOfWorkFactory(uow)

    opened = factory()

    assert opened.hooks == []
    assert factory.unit_of_work is uow
