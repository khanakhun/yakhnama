"""Unit tests for ``yakhnama.platform.uow`` with a recording session fake."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.unit.platform.events import FixedClock, make_event
from tests.unit.platform.sessions import RecordingSession
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.platform.uow import SqlAlchemyUnitOfWork, SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import InvariantViolationError

if TYPE_CHECKING:
    from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class SessionSource:
    """Hands out one ``RecordingSession`` per call and keeps them for assertions.

    Implements: Fake.

    Attributes:
        sessions: Every session created, in order.
    """

    def __init__(self) -> None:
        """Create the source with no sessions yet."""
        self.sessions: list[RecordingSession] = []

    def __call__(self) -> AsyncSession:
        """Return a new recording session."""
        session = RecordingSession()
        self.sessions.append(session)
        return session

    @property
    def only(self) -> RecordingSession:
        """Return the single session created so far."""
        (session,) = self.sessions
        return session


class ProbeUnitOfWork(SqlAlchemyUnitOfWork):
    """A module-style subclass that builds a "repository" on the session.

    Implements: Unit of Work.

    Attributes:
        opened_with: The session passed to ``_open_repositories``.
    """

    opened_with: AsyncSession | None = None

    def _open_repositories(self, session: AsyncSession) -> None:
        self.opened_with = session


@pytest.fixture
def source() -> SessionSource:
    return SessionSource()


@pytest.fixture
def unit_of_work(source: SessionSource) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(source, OutboxWriter(FixedClock()))


def test_sqlalchemy_unit_of_work_satisfies_the_kernel_protocols(
    source: SessionSource,
) -> None:
    writer = OutboxWriter(FixedClock())

    unit_of_work: UnitOfWork = SqlAlchemyUnitOfWork(source, writer)
    factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork] = SqlAlchemyUnitOfWorkFactory(
        SqlAlchemyUnitOfWork, session_factory=source, outbox_writer=writer
    )

    assert isinstance(unit_of_work, SqlAlchemyUnitOfWork)
    assert isinstance(factory(), SqlAlchemyUnitOfWork)


async def test_unit_of_work_commit_writes_events_to_outbox_then_commits_and_closes(
    unit_of_work: SqlAlchemyUnitOfWork, source: SessionSource
) -> None:
    events = [make_event("first"), make_event("second")]

    async with unit_of_work:
        for event in events:
            unit_of_work.record_event(event)
        await unit_of_work.commit()

    session = source.only
    assert [row.id for row in session.added if isinstance(row, OutboxMessage)] == [
        event.event_id for event in events
    ]
    assert session.calls == ["commit", "close"]
    assert unit_of_work.collected_events == ()


async def test_unit_of_work_collected_events_preserves_recording_order(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    first, second = make_event("first"), make_event("second")

    async with unit_of_work:
        unit_of_work.record_event(first)
        unit_of_work.record_event(second)
        collected = unit_of_work.collected_events

    assert collected == (first, second)


async def test_unit_of_work_exit_without_commit_rolls_back_and_writes_nothing(
    unit_of_work: SqlAlchemyUnitOfWork, source: SessionSource
) -> None:
    async with unit_of_work:
        unit_of_work.record_event(make_event())

    assert source.only.calls == ["rollback", "close"]
    assert source.only.added == []
    assert unit_of_work.collected_events == ()


async def test_unit_of_work_exception_inside_block_rolls_back_and_propagates(
    unit_of_work: SqlAlchemyUnitOfWork, source: SessionSource
) -> None:
    async def fail_inside_block() -> None:
        async with unit_of_work:
            unit_of_work.record_event(make_event())
            message = "use case failed"
            raise LookupError(message)

    with pytest.raises(LookupError, match="use case failed"):
        await fail_inside_block()

    assert source.only.calls == ["rollback", "close"]
    assert source.only.added == []


async def test_unit_of_work_explicit_rollback_discards_events(
    unit_of_work: SqlAlchemyUnitOfWork, source: SessionSource
) -> None:
    async with unit_of_work:
        unit_of_work.record_event(make_event())
        await unit_of_work.rollback()
        collected = unit_of_work.collected_events

    assert collected == ()
    assert source.only.calls == ["rollback", "rollback", "close"]


async def test_unit_of_work_commit_twice_raises_invariant_violation(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    async with unit_of_work:
        await unit_of_work.commit()

        with pytest.raises(InvariantViolationError, match="already committed"):
            await unit_of_work.commit()


async def test_unit_of_work_record_event_after_commit_raises_invariant_violation(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    async with unit_of_work:
        await unit_of_work.commit()

        with pytest.raises(InvariantViolationError, match="after the unit of work"):
            unit_of_work.record_event(make_event())


async def test_unit_of_work_failed_commit_keeps_events_and_rolls_back_on_exit(
    unit_of_work: SqlAlchemyUnitOfWork, source: SessionSource
) -> None:
    event = make_event()
    collected: list[object] = []

    async def commit_rejected_by_database() -> None:
        async with unit_of_work:
            source.only.commit_error = ConnectionError("database went away")
            unit_of_work.record_event(event)
            try:
                await unit_of_work.commit()
            finally:
                collected.extend(unit_of_work.collected_events)

    with pytest.raises(ConnectionError, match="database went away"):
        await commit_rejected_by_database()

    assert collected == [event]
    assert source.only.calls == ["commit", "rollback", "close"]


async def test_unit_of_work_entered_twice_raises_invariant_violation(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    async with unit_of_work:
        pass

    with pytest.raises(InvariantViolationError, match="entered only once"):
        await unit_of_work.__aenter__()


def test_unit_of_work_session_outside_block_raises_invariant_violation(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    with pytest.raises(InvariantViolationError, match="not active"):
        _ = unit_of_work.session


async def test_unit_of_work_commit_outside_block_raises_invariant_violation(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    with pytest.raises(InvariantViolationError, match="not active"):
        await unit_of_work.commit()


async def test_unit_of_work_session_after_exit_raises_invariant_violation(
    unit_of_work: SqlAlchemyUnitOfWork,
) -> None:
    async with unit_of_work:
        pass

    with pytest.raises(InvariantViolationError, match="not active"):
        _ = unit_of_work.session


async def test_unit_of_work_session_inside_block_is_the_factory_session(
    unit_of_work: SqlAlchemyUnitOfWork, source: SessionSource
) -> None:
    async with unit_of_work:
        session = unit_of_work.session

    assert session is source.only


async def test_unit_of_work_factory_opens_a_fresh_subclass_instance_each_call(
    source: SessionSource,
) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(
        ProbeUnitOfWork,
        session_factory=source,
        outbox_writer=OutboxWriter(FixedClock()),
    )

    first, second = factory(), factory()
    async with first:
        pass

    assert first is not second
    assert isinstance(first, ProbeUnitOfWork)
    assert first.opened_with is source.only
    assert second.opened_with is None
