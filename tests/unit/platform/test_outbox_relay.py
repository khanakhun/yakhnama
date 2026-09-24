"""Unit tests for ``yakhnama.platform.outbox.relay`` over an in-memory store.

The SQL behind the ``OutboxStore`` port (``SKIP LOCKED``, the lease filter, batch
purges) is covered by ``tests/integration/platform/test_outbox_relay_postgis.py``.
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from structlog.testing import capture_logs

from tests.unit.platform.events import (
    FIXED_INSTANT,
    FixedClock,
    SampleRecorded,
    make_event,
)
from tests.unit.platform.outbox_store import InMemoryOutboxStore
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.relay import (
    DEFAULT_LEASE_SECONDS,
    LAST_ERROR_MAX_LENGTH,
    MAX_BATCH_SIZE,
    OutboxRelay,
    RelayOutcome,
    Subscriber,
    SubscriberRegistry,
)
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.shared_kernel.errors import ConflictError, ValidationError

LEASE = timedelta(seconds=DEFAULT_LEASE_SECONDS)


class RecordingSubscriber:
    """Subscriber that records every envelope and optionally fails.

    Implements: Fake.

    Attributes:
        received: Envelopes received, in order.
        error: Raised on every call when set.
    """

    def __init__(self, error: Exception | None = None) -> None:
        """Create the subscriber."""
        self.received: list[OutboxEnvelope] = []
        self.error = error

    async def __call__(self, envelope: OutboxEnvelope) -> None:
        """Record ``envelope``, then raise ``error`` if set."""
        self.received.append(envelope)
        if self.error is not None:
            raise self.error


class SlowSubscriber:
    """Subscriber that never finishes within the relay's timeout.

    Implements: Fake.

    Attributes:
        was_cancelled: Whether the relay cancelled the call.
    """

    def __init__(self) -> None:
        """Create the subscriber."""
        self.was_cancelled = False

    async def __call__(self, envelope: OutboxEnvelope) -> None:
        """Wait far longer than any test timeout."""
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.was_cancelled = True
            raise


def _message(note: str = "first", clock: FixedClock | None = None) -> OutboxMessage:
    return OutboxWriter(clock or FixedClock()).to_message(make_event(note))


def _store(*notes: str) -> InMemoryOutboxStore:
    clock = FixedClock()
    store = InMemoryOutboxStore()
    for note in notes:
        store.add(_message(note, clock))
        # Distinct created_at values give the rows a defined delivery order.
        clock.advance(1)
    return store


def _relay(  # noqa: PLR0913  # reason: test builder mirroring the relay's keywords
    registry: SubscriberRegistry,
    store: InMemoryOutboxStore,
    clock: FixedClock | None = None,
    *,
    max_attempts: int = 5,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    subscriber_timeout_seconds: float = 30,
) -> OutboxRelay:
    return OutboxRelay(
        registry,
        clock if clock is not None else FixedClock(),
        store,
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
        subscriber_timeout_seconds=subscriber_timeout_seconds,
    )


def _registry(*subscribers: Subscriber) -> SubscriberRegistry:
    registry = SubscriberRegistry()
    for subscriber in subscribers:
        registry.subscribe(SampleRecorded.event_type, subscriber)
    return registry


def _only(store: InMemoryOutboxStore) -> OutboxMessage:
    (row,) = store.rows.values()
    return row


# --------------------------------------------------------------------------- #
# SubscriberRegistry                                                          #
# --------------------------------------------------------------------------- #


def test_subscriber_registry_subscribers_for_returns_registration_order() -> None:
    registry = SubscriberRegistry()
    first, second = RecordingSubscriber(), RecordingSubscriber()
    registry.subscribe(SampleRecorded.event_type, first)
    registry.subscribe(SampleRecorded.event_type, second)

    subscribers = registry.subscribers_for(SampleRecorded.event_type)

    assert subscribers == (first, second)


def test_subscriber_registry_subscribers_for_unknown_event_type_is_empty() -> None:
    registry = SubscriberRegistry()

    subscribers = registry.subscribers_for("testing.nobody_listens")

    assert subscribers == ()


@pytest.mark.parametrize("event_type", ["", "NoContext", "testing.Upper", "a.b.c"])
def test_subscriber_registry_subscribe_malformed_event_type_raises_validation_error(
    event_type: str,
) -> None:
    registry = SubscriberRegistry()

    with pytest.raises(ValidationError, match="malformed event_type"):
        registry.subscribe(event_type, RecordingSubscriber())


def test_subscriber_registry_subscribe_same_subscriber_twice_raises_conflict() -> None:
    registry = SubscriberRegistry()
    subscriber = RecordingSubscriber()
    registry.subscribe(SampleRecorded.event_type, subscriber)

    with pytest.raises(ConflictError, match="already registered"):
        registry.subscribe(SampleRecorded.event_type, subscriber)


# --------------------------------------------------------------------------- #
# Construction and arguments                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("keywords", "match"),
    [
        ({"max_attempts": 0}, "max_attempts"),
        ({"subscriber_timeout_seconds": 0}, "subscriber_timeout_seconds must be"),
        (
            {"lease_seconds": 10, "subscriber_timeout_seconds": 11},
            "lease_seconds must be at least",
        ),
    ],
)
def test_outbox_relay_invalid_bound_raises_validation_error(
    keywords: dict[str, int], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        OutboxRelay(
            SubscriberRegistry(), FixedClock(), InMemoryOutboxStore(), **keywords
        )


@pytest.mark.parametrize("batch_size", [0, MAX_BATCH_SIZE + 1])
async def test_outbox_relay_relay_once_batch_size_out_of_range_raises_before_io(
    batch_size: int,
) -> None:
    store = _store("first")

    with pytest.raises(ValidationError, match="batch_size"):
        await _relay(SubscriberRegistry(), store).relay_once(batch_size)

    assert store.claims == []


# --------------------------------------------------------------------------- #
# Claim, dispatch, publish                                                    #
# --------------------------------------------------------------------------- #


async def test_outbox_relay_relay_once_claims_with_lease_from_clock() -> None:
    store = _store("first")
    clock = FixedClock(FIXED_INSTANT + timedelta(minutes=1))

    await _relay(SubscriberRegistry(), store, clock).relay_once(25)

    (claim,) = store.claims
    assert claim.now == FIXED_INSTANT + timedelta(minutes=1)
    assert claim.lease_until == claim.now + LEASE
    assert claim.max_attempts == 5
    assert claim.batch_size == 25


async def test_outbox_relay_relay_once_success_publishes_and_releases_lease() -> None:
    subscriber = RecordingSubscriber()
    store = _store("first")
    clock = FixedClock(FIXED_INSTANT + timedelta(minutes=1))

    outcome = await _relay(_registry(subscriber), store, clock).relay_once(10)

    row = _only(store)
    assert outcome == RelayOutcome(claimed=1, published=1, failed=0)
    assert row.published_at == FIXED_INSTANT + timedelta(minutes=1)
    assert row.leased_until is None
    assert row.attempts == 1
    assert [envelope.event_id for envelope in subscriber.received] == [row.id]


async def test_outbox_relay_relay_once_delivers_oldest_first_within_batch() -> None:
    subscriber = RecordingSubscriber()
    store = _store("first", "second", "third")

    outcome = await _relay(_registry(subscriber), store).relay_once(2)

    assert outcome.claimed == 2
    assert [envelope.payload["note"] for envelope in subscriber.received] == [
        "first",
        "second",
    ]


async def test_outbox_relay_relay_once_published_row_is_not_claimed_again() -> None:
    subscriber = RecordingSubscriber()
    store = _store("first")
    relay = _relay(_registry(subscriber), store)
    await relay.relay_once(10)

    second = await relay.relay_once(10)

    assert second == RelayOutcome(claimed=0, published=0, failed=0)
    assert len(subscriber.received) == 1


async def test_outbox_relay_relay_once_without_subscribers_marks_published() -> None:
    store = _store("first")

    outcome = await _relay(SubscriberRegistry(), store).relay_once(10)

    assert outcome.published == 1
    assert _only(store).published_at == FIXED_INSTANT


async def test_outbox_relay_relay_once_leased_row_is_skipped_until_lease_lapses() -> (
    None
):
    subscriber = RecordingSubscriber()
    store = _store("first")
    clock = FixedClock()
    row = _only(store)
    # Another relay holds the lease until one second after now.
    row.leased_until = clock.now() + timedelta(seconds=1)
    relay = _relay(_registry(subscriber), store, clock)

    while_leased = await relay.relay_once(10)
    clock.advance(2)
    after_lapse = await relay.relay_once(10)

    assert while_leased.claimed == 0
    assert after_lapse.published == 1
    assert row.attempts == 1


# --------------------------------------------------------------------------- #
# Failures, timeouts, dead-letter                                             #
# --------------------------------------------------------------------------- #


async def test_outbox_relay_relay_once_failure_records_error_and_clears_lease() -> None:
    store = _store("first")

    outcome = await _relay(
        _registry(RecordingSubscriber(RuntimeError("storage unavailable"))), store
    ).relay_once(10)

    row = _only(store)
    assert outcome == RelayOutcome(claimed=1, published=0, failed=1)
    assert row.published_at is None
    assert row.leased_until is None
    assert row.dead_lettered_at is None
    assert row.attempts == 1
    assert row.last_error == "RecordingSubscriber: RuntimeError"


async def test_outbox_relay_relay_once_failed_row_is_retried_on_next_run() -> None:
    store = _store("first")
    subscriber = RecordingSubscriber(RuntimeError("once"))
    relay = _relay(_registry(subscriber), store)
    await relay.relay_once(10)
    subscriber.error = None

    retry = await relay.relay_once(10)

    row = _only(store)
    assert retry.published == 1
    assert row.attempts == 2
    assert row.last_error == "RecordingSubscriber: RuntimeError"


async def test_outbox_relay_relay_once_one_failing_subscriber_still_calls_others() -> (
    None
):
    healthy = RecordingSubscriber()
    store = _store("first")

    await _relay(
        _registry(RecordingSubscriber(ValueError("bad")), healthy), store
    ).relay_once(10)

    assert len(healthy.received) == 1
    assert _only(store).published_at is None


async def test_outbox_relay_relay_once_function_subscriber_is_named_in_last_error() -> (
    None
):
    async def audit_subscriber(envelope: OutboxEnvelope) -> None:
        raise LookupError(envelope.event_type)

    store = _store("first")

    await _relay(_registry(audit_subscriber), store).relay_once(10)

    last_error = _only(store).last_error
    assert last_error is not None
    assert "audit_subscriber: LookupError" in last_error


async def test_outbox_relay_relay_once_error_text_and_nul_never_reach_last_error() -> (
    None
):
    marker = "phone=0300-1234567"
    store = _store("first")
    failing = RecordingSubscriber(RuntimeError(f"DETAIL: Key ({marker}) \x00tail"))

    await _relay(_registry(failing), store).relay_once(10)

    assert _only(store).last_error == "RecordingSubscriber: RuntimeError"


async def test_outbox_relay_relay_once_many_failing_subscribers_is_truncated() -> None:
    store = _store("first")
    registry = SubscriberRegistry()
    for _ in range(200):
        registry.subscribe(SampleRecorded.event_type, RecordingSubscriber(KeyError()))

    await _relay(registry, store).relay_once(10)

    last_error = _only(store).last_error
    assert last_error is not None
    assert len(last_error) == LAST_ERROR_MAX_LENGTH


async def test_outbox_relay_relay_once_corrupt_payload_records_envelope_failure() -> (
    None
):
    subscriber = RecordingSubscriber()
    store = _store("first")
    _only(store).occurred_at = FIXED_INSTANT.replace(tzinfo=None)

    outcome = await _relay(_registry(subscriber), store).relay_once(10)

    assert outcome.failed == 1
    assert _only(store).last_error == "envelope: ValidationError"
    assert subscriber.received == []


async def test_outbox_relay_relay_once_counts_mixed_batch() -> None:
    async def fail_on_second(envelope: OutboxEnvelope) -> None:
        if envelope.payload["note"] == "second":
            message = "second fails"
            raise RuntimeError(message)

    store = _store("first", "second", "third")

    outcome = await _relay(_registry(fail_on_second), store).relay_once(10)

    assert outcome == RelayOutcome(claimed=3, published=2, failed=1)
    assert sorted(
        str(row.payload["note"]) for row in store.rows.values() if row.published_at
    ) == ["first", "third"]


async def test_outbox_relay_relay_once_timeout_counts_as_failure_and_cancels() -> None:
    slow = SlowSubscriber()
    store = _store("first")

    outcome = await _relay(
        _registry(slow), store, subscriber_timeout_seconds=0.01
    ).relay_once(10)

    assert outcome.failed == 1
    assert slow.was_cancelled is True
    assert _only(store).last_error == "SlowSubscriber: TimeoutError"


async def test_outbox_relay_relay_once_last_attempt_failure_dead_letters() -> None:
    store = _store("first")
    clock = FixedClock()
    relay = _relay(
        _registry(RecordingSubscriber(RuntimeError("down"))),
        store,
        clock,
        max_attempts=2,
    )
    first = await relay.relay_once(10)
    clock.advance(1)

    with capture_logs() as logs:
        second = await relay.relay_once(10)
    third = await relay.relay_once(10)

    row = _only(store)
    assert (first.dead_lettered, second.dead_lettered) == (0, 1)
    assert third.claimed == 0
    assert row.attempts == 2
    assert row.dead_lettered_at == FIXED_INSTANT + timedelta(seconds=1)
    assert {
        "event": "outbox_dead_lettered",
        "log_level": "warning",
        "event_id": str(row.id),
        "event_type": SampleRecorded.event_type,
        "attempts": 2,
    } in logs


async def test_outbox_relay_relay_once_crashed_last_attempt_is_dead_lettered() -> None:
    store = _store("first")
    clock = FixedClock()
    row = _only(store)
    # A relay claimed the last attempt and died: the lease is live, nobody records.
    row.attempts = 3
    row.leased_until = clock.now() + LEASE
    relay = _relay(_registry(RecordingSubscriber()), store, clock, max_attempts=3)

    while_leased = await relay.relay_once(10)
    clock.advance(LEASE.total_seconds() + 1)
    after_lapse = await relay.relay_once(10)

    assert while_leased.dead_lettered == 0
    assert after_lapse == RelayOutcome(
        claimed=0, published=0, failed=0, dead_lettered=1
    )
    assert row.dead_lettered_at == clock.now()
    assert row.leased_until is None


async def test_outbox_relay_relay_once_lost_lease_keeps_other_relays_outcome() -> None:
    store = _store("first")
    clock = FixedClock()
    other_lease = clock.now() + timedelta(hours=1)

    async def overtaken(envelope: OutboxEnvelope) -> None:
        # While this call ran, our lease lapsed and another relay claimed the row.
        store.rows[envelope.event_id].leased_until = other_lease
        message = "slow and failing"
        raise RuntimeError(message)

    with capture_logs() as logs:
        outcome = await _relay(_registry(overtaken), store, clock).relay_once(10)

    row = _only(store)
    assert outcome.failed == 1
    assert row.leased_until == other_lease
    assert row.last_error is None
    assert logs[-1] == {
        "event": "outbox_lease_lost",
        "log_level": "warning",
        "event_id": str(row.id),
        "event_type": SampleRecorded.event_type,
    }


async def test_outbox_relay_relay_once_store_failure_after_delivery_propagates() -> (
    None
):
    store = _store("first")
    store.failure_on_mark = ConnectionError("database gone")

    with pytest.raises(ConnectionError):
        await _relay(_registry(RecordingSubscriber()), store).relay_once(10)

    # The lease stays; the row is claimed again once it lapses.
    assert _only(store).leased_until == FIXED_INSTANT + LEASE


async def test_outbox_relay_relay_once_failure_log_omits_error_message() -> None:
    store = _store("first")
    failing = RecordingSubscriber(RuntimeError("reporter phone 0300-0000000"))

    with capture_logs() as logs:
        await _relay(_registry(failing), store).relay_once(10)

    assert logs == [
        {
            "event": "outbox_delivery_failed",
            "log_level": "warning",
            "event_id": str(_only(store).id),
            "event_type": SampleRecorded.event_type,
            "subscriber": "RecordingSubscriber",
            "error_type": "RuntimeError",
        }
    ]


# --------------------------------------------------------------------------- #
# Retention                                                                   #
# --------------------------------------------------------------------------- #


async def test_outbox_relay_purge_published_deletes_only_old_published_rows() -> None:
    store = _store("old", "recent", "pending")
    old, recent, pending = sorted(store.rows.values(), key=lambda row: row.created_at)
    old.published_at = FIXED_INSTANT - timedelta(days=31)
    recent.published_at = FIXED_INSTANT - timedelta(days=1)
    cutoff = FIXED_INSTANT - timedelta(days=30)

    with capture_logs() as logs:
        deleted = await _relay(SubscriberRegistry(), store).purge_published(cutoff)

    assert deleted == 1
    assert set(store.rows) == {recent.id, pending.id}
    assert logs == [{"event": "outbox_purged", "log_level": "info", "deleted": 1}]


async def test_outbox_relay_purge_published_naive_cutoff_raises_validation_error() -> (
    None
):
    store = _store("first")
    naive = datetime(2026, 9, 1)  # noqa: DTZ001  # reason: the naive value under test

    with pytest.raises(ValidationError, match="timezone-aware"):
        await _relay(SubscriberRegistry(), store).purge_published(naive)

    assert len(store.rows) == 1
