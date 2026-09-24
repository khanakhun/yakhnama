"""``OutboxRelay`` over ``SqlAlchemyOutboxStore`` against real PostGIS.

Covers what the in-memory Fake cannot: ``FOR UPDATE SKIP LOCKED``, the claim
committing before any subscriber runs, concurrent relays, lease expiry after a crash,
dead-lettering, the check constraint and batched retention purges.
"""

import asyncio
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.unit.platform.events import (
    FIXED_INSTANT,
    FixedClock,
    SampleRecorded,
    make_event,
)
from yakhnama.platform.container import Container
from yakhnama.platform.outbox import sqlalchemy_store
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.relay import OutboxRelay, SubscriberRegistry
from yakhnama.platform.outbox.sqlalchemy_store import SqlAlchemyOutboxStore
from yakhnama.platform.outbox.store import LeaseClaim
from yakhnama.platform.outbox.writer import OutboxWriter

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("schema")]

LEASE_SECONDS = 60


class CollectingSubscriber:
    """Records the ids it receives; fails for ids in ``failing``; may pause.

    Implements: Fake.

    Attributes:
        received: Event ids received, in order.
        failing: Event ids to reject.
        pause_seconds: How long each call sleeps first, to widen race windows.
    """

    def __init__(
        self, failing: frozenset[UUID] = frozenset(), pause_seconds: float = 0
    ) -> None:
        """Create the subscriber."""
        self.received: list[UUID] = []
        self.failing = failing
        self.pause_seconds = pause_seconds

    async def __call__(self, envelope: OutboxEnvelope) -> None:
        """Record the envelope, then reject it if its id is in ``failing``."""
        if self.pause_seconds:
            await asyncio.sleep(self.pause_seconds)
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


def _relay(
    container: Container,
    subscriber: CollectingSubscriber,
    clock: FixedClock | None = None,
    *,
    max_attempts: int = 5,
) -> OutboxRelay:
    registry = SubscriberRegistry()
    registry.subscribe(SampleRecorded.event_type, subscriber)
    return OutboxRelay(
        registry,
        clock or FixedClock(),
        SqlAlchemyOutboxStore(container.session_factory),
        max_attempts=max_attempts,
        lease_seconds=LEASE_SECONDS,
        subscriber_timeout_seconds=5,
    )


async def test_relay_once_delivers_pending_rows_oldest_first_and_marks_them(
    container: Container,
) -> None:
    ids = await _store_messages(container, 3)
    subscriber = CollectingSubscriber()
    relay = _relay(container, subscriber)

    first = await relay.relay_once(batch_size=10)
    second = await relay.relay_once(batch_size=10)

    rows = await _load(container)
    assert (first.claimed, first.published, first.failed) == (3, 3, 0)
    assert second.claimed == 0
    assert subscriber.received == ids
    assert all(row.published_at == FIXED_INSTANT for row in rows.values())
    assert all(row.leased_until is None for row in rows.values())
    assert all(row.attempts == 1 for row in rows.values())


async def test_relay_once_batch_size_limits_the_claim(container: Container) -> None:
    ids = await _store_messages(container, 3)
    subscriber = CollectingSubscriber()

    outcome = await _relay(container, subscriber).relay_once(batch_size=2)

    assert outcome.claimed == 2
    assert subscriber.received == ids[:2]


async def test_relay_once_commits_the_lease_before_subscribers_run(
    container: Container,
) -> None:
    (event_id,) = await _store_messages(container, 1)
    seen: list[tuple[object, int]] = []

    async def inspect_row(envelope: OutboxEnvelope) -> None:
        # NOWAIT fails at once if the relay still held the row lock.
        async with container.session_factory() as other, other.begin():
            row = await other.scalar(
                select(OutboxMessage)
                .where(OutboxMessage.id == envelope.event_id)
                .with_for_update(nowait=True)
            )
            assert row is not None
            seen.append((row.leased_until, row.attempts))

    registry = SubscriberRegistry()
    registry.subscribe(SampleRecorded.event_type, inspect_row)
    relay = OutboxRelay(
        registry,
        FixedClock(),
        SqlAlchemyOutboxStore(container.session_factory),
        lease_seconds=LEASE_SECONDS,
    )

    outcome = await relay.relay_once(batch_size=1)

    assert outcome.published == 1
    assert seen == [(FIXED_INSTANT + timedelta(seconds=LEASE_SECONDS), 1)]
    assert (await _load(container))[event_id].published_at is not None


async def test_relay_once_skips_rows_locked_by_another_transaction(
    container: Container,
) -> None:
    ids = await _store_messages(container, 3)
    subscriber = CollectingSubscriber()
    relay = _relay(container, subscriber)

    async with container.session_factory() as other, other.begin():
        await other.execute(
            select(OutboxMessage).where(OutboxMessage.id == ids[0]).with_for_update()
        )
        while_locked = await relay.relay_once(batch_size=10)
    after_release = await relay.relay_once(batch_size=10)

    assert while_locked.claimed == 2
    assert after_release.claimed == 1
    assert subscriber.received == [ids[1], ids[2], ids[0]]


async def test_two_relays_racing_on_one_batch_never_double_dispatch(
    container: Container,
) -> None:
    ids = await _store_messages(container, 40)
    subscriber = CollectingSubscriber(pause_seconds=0.005)
    first, second = _relay(container, subscriber), _relay(container, subscriber)

    outcomes = await asyncio.gather(
        first.relay_once(batch_size=40), second.relay_once(batch_size=40)
    )

    rows = await _load(container)
    assert sum(outcome.claimed for outcome in outcomes) == 40
    assert sorted(subscriber.received) == sorted(ids)
    assert len(set(subscriber.received)) == 40
    assert all(row.attempts == 1 for row in rows.values())


async def test_crashed_relay_lease_expires_and_the_row_is_retried(
    container: Container,
) -> None:
    (event_id,) = await _store_messages(container, 1)
    clock = FixedClock()
    store = SqlAlchemyOutboxStore(container.session_factory)
    # A relay claims the row and dies before recording anything.
    await store.claim(
        LeaseClaim(
            now=clock.now(),
            lease_until=clock.now() + timedelta(seconds=LEASE_SECONDS),
            max_attempts=5,
            batch_size=10,
        )
    )
    subscriber = CollectingSubscriber()
    relay = _relay(container, subscriber, clock)

    while_leased = await relay.relay_once(batch_size=10)
    clock.advance(LEASE_SECONDS + 1)
    after_expiry = await relay.relay_once(batch_size=10)

    row = (await _load(container))[event_id]
    assert while_leased.claimed == 0
    assert after_expiry.published == 1
    assert row.attempts == 2
    assert subscriber.received == [event_id]


async def test_relay_once_failure_persists_error_and_releases_lease(
    container: Container,
) -> None:
    ids = await _store_messages(container, 2)
    subscriber = CollectingSubscriber(failing=frozenset({ids[0]}))

    outcome = await _relay(container, subscriber).relay_once(batch_size=10)

    rows = await _load(container)
    assert (outcome.published, outcome.failed) == (1, 1)
    assert rows[ids[0]].published_at is None
    assert rows[ids[0]].leased_until is None
    assert rows[ids[0]].attempts == 1
    assert rows[ids[0]].last_error == "CollectingSubscriber: RuntimeError"
    assert rows[ids[1]].published_at is not None


async def test_relay_once_dead_letters_after_max_attempts(container: Container) -> None:
    ids = await _store_messages(container, 1)
    clock = FixedClock()
    subscriber = CollectingSubscriber(failing=frozenset(ids))
    relay = _relay(container, subscriber, clock, max_attempts=2)

    outcomes = []
    for _ in range(3):
        outcomes.append(await relay.relay_once(batch_size=10))
        clock.advance(1)

    row = (await _load(container))[ids[0]]
    assert [outcome.claimed for outcome in outcomes] == [1, 1, 0]
    assert [outcome.dead_lettered for outcome in outcomes] == [0, 1, 0]
    assert row.attempts == 2
    assert row.published_at is None
    assert row.dead_lettered_at == FIXED_INSTANT + timedelta(seconds=1)
    assert len(subscriber.received) == 2


async def test_crashed_last_attempt_is_dead_lettered_once_the_lease_lapses(
    container: Container,
) -> None:
    (event_id,) = await _store_messages(container, 1)
    clock = FixedClock()
    store = SqlAlchemyOutboxStore(container.session_factory)
    await store.claim(
        LeaseClaim(
            now=clock.now(),
            lease_until=clock.now() + timedelta(seconds=LEASE_SECONDS),
            max_attempts=1,
            batch_size=1,
        )
    )
    relay = _relay(container, CollectingSubscriber(), clock, max_attempts=1)

    while_leased = await relay.relay_once(batch_size=10)
    clock.advance(LEASE_SECONDS + 1)
    after_lapse = await relay.relay_once(batch_size=10)

    row = (await _load(container))[event_id]
    assert while_leased.dead_lettered == 0
    assert after_lapse.dead_lettered == 1
    assert row.dead_lettered_at == clock.now()
    assert row.leased_until is None


async def test_record_failure_with_a_lost_lease_changes_nothing(
    container: Container,
) -> None:
    (event_id,) = await _store_messages(container, 1)
    store = SqlAlchemyOutboxStore(container.session_factory)
    lease = FIXED_INSTANT + timedelta(seconds=LEASE_SECONDS)
    await store.claim(
        LeaseClaim(now=FIXED_INSTANT, lease_until=lease, max_attempts=5, batch_size=1)
    )

    is_recorded = await store.record_failure(
        event_id, lease - timedelta(seconds=1), "Other: RuntimeError", None
    )

    row = (await _load(container))[event_id]
    assert is_recorded is False
    assert row.leased_until == lease
    assert row.last_error is None


async def test_mark_published_clears_a_dead_letter_and_is_not_repeated(
    container: Container,
) -> None:
    (event_id,) = await _store_messages(container, 1)
    store = SqlAlchemyOutboxStore(container.session_factory)
    async with container.session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE outbox_messages SET dead_lettered_at = :at"),
            {"at": FIXED_INSTANT},
        )

    first = await store.mark_published(event_id, FIXED_INSTANT)
    second = await store.mark_published(event_id, FIXED_INSTANT)

    row = (await _load(container))[event_id]
    assert (first, second) == (True, False)
    assert row.dead_lettered_at is None
    assert row.published_at == FIXED_INSTANT


async def test_outbox_row_both_published_and_dead_lettered_is_rejected(
    container: Container,
) -> None:
    await _store_messages(container, 1)

    with pytest.raises(IntegrityError, match="published_or_dead_lettered"):
        async with container.session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE outbox_messages SET published_at = :at,"
                    " dead_lettered_at = :at"
                ),
                {"at": FIXED_INSTANT},
            )


async def test_purge_published_deletes_old_published_rows_in_batches(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _store_messages(container, 7)
    cutoff = FIXED_INSTANT - timedelta(days=30)
    async with container.session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE outbox_messages SET published_at = :at WHERE id = ANY(:ids)"),
            {"at": cutoff - timedelta(seconds=1), "ids": ids[:5]},
        )
        await session.execute(
            text("UPDATE outbox_messages SET published_at = :at WHERE id = :id"),
            {"at": cutoff, "id": ids[5]},
        )
    # Two rows per batch, so five old rows take three batches.
    monkeypatch.setattr(sqlalchemy_store, "PURGE_BATCH_SIZE", 2)
    relay = _relay(container, CollectingSubscriber())

    deleted = await relay.purge_published(cutoff)

    remaining = await _load(container)
    assert deleted == 5
    assert set(remaining) == {ids[5], ids[6]}
