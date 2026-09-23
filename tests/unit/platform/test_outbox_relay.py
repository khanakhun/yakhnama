"""Unit tests for the delivery bookkeeping in ``yakhnama.platform.outbox.relay``.

``relay_once``'s claim query needs PostgreSQL; it is covered by
``tests/integration/platform/test_outbox_relay_postgis.py``.
"""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from tests.unit.platform.events import (
    FIXED_INSTANT,
    FixedClock,
    SampleRecorded,
    make_event,
)
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.platform.outbox.relay import (
    LAST_ERROR_MAX_LENGTH,
    MAX_BATCH_SIZE,
    OutboxRelay,
    RelayOutcome,
    SubscriberRegistry,
)
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.shared_kernel.errors import ConflictError, ValidationError


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


def _message(note: str = "first") -> OutboxMessage:
    return OutboxWriter(FixedClock()).to_message(make_event(note))


def _relay(
    registry: SubscriberRegistry, clock: FixedClock | None = None
) -> OutboxRelay:
    return OutboxRelay(registry, clock if clock is not None else FixedClock())


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


def test_outbox_relay_max_attempts_below_one_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="max_attempts"):
        OutboxRelay(SubscriberRegistry(), FixedClock(), max_attempts=0)


@pytest.mark.parametrize("batch_size", [0, MAX_BATCH_SIZE + 1])
async def test_outbox_relay_relay_once_batch_size_out_of_range_raises_before_io(
    batch_size: int,
) -> None:
    opened: list[AsyncSession] = []

    def session_factory() -> AsyncSession:
        session = AsyncSession()
        opened.append(session)
        return session

    with pytest.raises(ValidationError, match="batch_size"):
        await _relay(SubscriberRegistry()).relay_once(session_factory, batch_size)

    assert opened == []


async def test_outbox_relay_deliver_success_marks_published_at_from_clock() -> None:
    registry = SubscriberRegistry()
    subscriber = RecordingSubscriber()
    registry.subscribe(SampleRecorded.event_type, subscriber)
    clock = FixedClock(FIXED_INSTANT + timedelta(minutes=1))
    message = _message()

    outcome = await _relay(registry, clock).deliver([message])

    assert outcome == RelayOutcome(claimed=1, published=1, failed=0)
    assert message.published_at == FIXED_INSTANT + timedelta(minutes=1)
    assert message.attempts == 0
    assert [envelope.event_id for envelope in subscriber.received] == [message.id]


async def test_outbox_relay_deliver_subscriber_failure_records_attempt_and_error() -> (
    None
):
    registry = SubscriberRegistry()
    failing = RecordingSubscriber(RuntimeError("storage unavailable"))
    registry.subscribe(SampleRecorded.event_type, failing)
    message = _message()

    outcome = await _relay(registry).deliver([message])

    assert outcome == RelayOutcome(claimed=1, published=0, failed=1)
    assert message.published_at is None
    assert message.attempts == 1
    assert message.last_error == "RecordingSubscriber: RuntimeError"


async def test_outbox_relay_deliver_one_failing_subscriber_still_calls_the_others() -> (
    None
):
    registry = SubscriberRegistry()
    failing = RecordingSubscriber(ValueError("bad"))
    healthy = RecordingSubscriber()
    registry.subscribe(SampleRecorded.event_type, failing)
    registry.subscribe(SampleRecorded.event_type, healthy)
    message = _message()

    await _relay(registry).deliver([message])

    assert len(healthy.received) == 1
    assert message.published_at is None


async def test_outbox_relay_deliver_function_subscriber_is_named_in_last_error() -> (
    None
):
    async def audit_subscriber(envelope: OutboxEnvelope) -> None:
        raise LookupError(envelope.event_type)

    registry = SubscriberRegistry()
    registry.subscribe(SampleRecorded.event_type, audit_subscriber)
    message = _message()

    await _relay(registry).deliver([message])

    assert message.last_error is not None
    assert "audit_subscriber: LookupError" in message.last_error


async def test_outbox_relay_deliver_error_text_and_nul_never_reach_last_error() -> None:
    marker = "phone=0300-1234567"
    registry = SubscriberRegistry()
    registry.subscribe(
        SampleRecorded.event_type,
        RecordingSubscriber(RuntimeError(f"DETAIL: Key ({marker}) exists\x00tail")),
    )
    message = _message()

    await _relay(registry).deliver([message])

    assert message.last_error == "RecordingSubscriber: RuntimeError"
    assert marker not in message.last_error
    assert "\x00" not in message.last_error


async def test_outbox_relay_deliver_many_failing_subscribers_is_truncated() -> None:
    registry = SubscriberRegistry()
    for _ in range(200):
        registry.subscribe(SampleRecorded.event_type, RecordingSubscriber(KeyError()))
    message = _message()

    await _relay(registry).deliver([message])

    assert message.last_error is not None
    assert len(message.last_error) == LAST_ERROR_MAX_LENGTH


async def test_outbox_relay_deliver_without_subscribers_marks_published() -> None:
    message = _message()

    outcome = await _relay(SubscriberRegistry()).deliver([message])

    assert outcome.published == 1
    assert message.published_at == FIXED_INSTANT


async def test_outbox_relay_deliver_corrupt_payload_records_envelope_failure() -> None:
    registry = SubscriberRegistry()
    subscriber = RecordingSubscriber()
    registry.subscribe(SampleRecorded.event_type, subscriber)
    message = _message()
    message.occurred_at = FIXED_INSTANT.replace(tzinfo=None)

    outcome = await _relay(registry).deliver([message])

    assert outcome.failed == 1
    assert message.attempts == 1
    assert message.last_error is not None
    assert message.last_error == "envelope: ValidationError"
    assert subscriber.received == []


async def test_outbox_relay_deliver_counts_mixed_batch() -> None:
    registry = SubscriberRegistry()

    async def fail_on_second(envelope: OutboxEnvelope) -> None:
        if envelope.payload["note"] == "second":
            message = "second fails"
            raise RuntimeError(message)

    registry.subscribe(SampleRecorded.event_type, fail_on_second)
    messages = [_message("first"), _message("second"), _message("third")]

    outcome = await _relay(registry).deliver(messages)

    assert outcome == RelayOutcome(claimed=3, published=2, failed=1)
    assert [message.published_at is None for message in messages] == [
        False,
        True,
        False,
    ]


async def test_outbox_relay_deliver_failure_log_omits_error_message() -> None:
    registry = SubscriberRegistry()
    registry.subscribe(
        SampleRecorded.event_type,
        RecordingSubscriber(RuntimeError("reporter phone 0300-0000000")),
    )
    message = _message()

    with capture_logs() as logs:
        await _relay(registry).deliver([message])

    assert logs == [
        {
            "event": "outbox_delivery_failed",
            "log_level": "warning",
            "event_id": str(message.id),
            "event_type": SampleRecorded.event_type,
            "subscriber": "RecordingSubscriber",
            "error_type": "RuntimeError",
        }
    ]
