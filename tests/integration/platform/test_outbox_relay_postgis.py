"""``OutboxRelay.relay_once`` against real PostGIS, including ``SKIP LOCKED``."""

from uuid import UUID

import pytest
from sqlalchemy import select

from tests.unit.platform.events import FixedClock, SampleRecorded, make_event
from yakhnama.platform.container import Container
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.writer import OutboxWriter

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("schema")]


class CollectingSubscriber:
    """Records the ids it receives; fails for ids in ``failing``.

    Implements: Fake.

    Attributes:
        received: Event ids received, in order.
        failing: Event ids to reject.
    """

    def __init__(self, failing: frozenset[UUID] = frozenset()) -> None:
        """Create the subscriber."""
        self.received: list[UUID] = []
        self.failing = failing

    async def __call__(self, envelope: OutboxEnvelope) -> None:
        """Record the envelope, then reject it if its id is in ``failing``."""
        self.received.append(envelope.event_id)
        if envelope.event_id in self.failing:
            message = "subscriber rejected the event"
            raise RuntimeError(message)


async def _store_messages(container: Container, count: int) -> list[UUID]:
    # A dedicated clock gives each row a distinct, increasing created_at.
    clock = FixedClock()
    writer = OutboxWriter(clock)
    events = [make_event(f"note {index}") for index in range(count)]
    async with container.session_factory() as session, session.begin():
        for event in events:
            session.add(writer.to_message(event))
            clock.advance(1)
    return [event.event_id for event in events]


async def _load(container: Container) -> dict[UUID, OutboxMessage]:
    async with container.session_factory() as session:
        rows = await session.scalars(select(OutboxMessage))
        return {row.id: row for row in rows}


def _relay(subscriber: CollectingSubscriber, *, max_attempts: int = 5) -> OutboxRelay:
    registry = SubscriberRegistry()
    registry.subscribe(SampleRecorded.event_type, subscriber)
    return OutboxRelay(registry, FixedClock(), max_attempts=max_attempts)


async def test_relay_once_delivers_pending_rows_oldest_first_and_marks_them(
    container: Container,
) -> None:
    ids = await _store_messages(container, 3)
    subscriber = CollectingSubscriber()
    relay = _relay(subscriber)

    first = await relay.relay_once(container.session_factory, batch_size=10)
    second = await relay.relay_once(container.session_factory, batch_size=10)

    rows = await _load(container)
    assert (first.claimed, first.published, first.failed) == (3, 3, 0)
    assert second.claimed == 0
    assert subscriber.received == ids
    assert all(row.published_at is not None for row in rows.values())


async def test_relay_once_batch_size_limits_the_claim(container: Container) -> None:
    ids = await _store_messages(container, 3)
    subscriber = CollectingSubscriber()

    outcome = await _relay(subscriber).relay_once(
        container.session_factory, batch_size=2
    )

    assert outcome.claimed == 2
    assert subscriber.received == ids[:2]


async def test_relay_once_skips_rows_locked_by_another_transaction(
    container: Container,
) -> None:
    ids = await _store_messages(container, 3)
    subscriber = CollectingSubscriber()
    relay = _relay(subscriber)

    async with container.session_factory() as other, other.begin():
        await other.execute(
            select(OutboxMessage).where(OutboxMessage.id == ids[0]).with_for_update()
        )
        while_locked = await relay.relay_once(container.session_factory, batch_size=10)
    after_release = await relay.relay_once(container.session_factory, batch_size=10)

    assert while_locked.claimed == 2
    assert after_release.claimed == 1
    assert subscriber.received == [ids[1], ids[2], ids[0]]


async def test_relay_once_failure_persists_attempts_and_last_error(
    container: Container,
) -> None:
    ids = await _store_messages(container, 2)
    subscriber = CollectingSubscriber(failing=frozenset({ids[0]}))

    outcome = await _relay(subscriber).relay_once(
        container.session_factory, batch_size=10
    )

    rows = await _load(container)
    assert (outcome.published, outcome.failed) == (1, 1)
    assert rows[ids[0]].published_at is None
    assert rows[ids[0]].attempts == 1
    assert rows[ids[0]].last_error == (
        "CollectingSubscriber: RuntimeError: subscriber rejected the event"
    )
    assert rows[ids[1]].published_at is not None


async def test_relay_once_stops_claiming_a_row_after_max_attempts(
    container: Container,
) -> None:
    ids = await _store_messages(container, 1)
    subscriber = CollectingSubscriber(failing=frozenset(ids))
    relay = _relay(subscriber, max_attempts=2)

    outcomes = [
        await relay.relay_once(container.session_factory, batch_size=10)
        for _ in range(3)
    ]

    rows = await _load(container)
    assert [outcome.claimed for outcome in outcomes] == [1, 1, 0]
    assert rows[ids[0]].attempts == 2
    assert rows[ids[0]].published_at is None
