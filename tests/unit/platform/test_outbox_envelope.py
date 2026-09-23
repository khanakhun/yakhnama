"""Unit tests for ``yakhnama.platform.outbox.envelope``."""

import pydantic
import pytest

from tests.unit.platform.events import (
    FixedClock,
    OtherRecorded,
    SampleRecorded,
    make_event,
)
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.platform.outbox.writer import OutboxWriter
from yakhnama.shared_kernel.errors import ValidationError


def test_outbox_envelope_from_message_round_trips_to_the_original_event() -> None:
    event = make_event("debris flow")
    message = OutboxWriter(FixedClock()).to_message(event)

    rebuilt = OutboxEnvelope.from_message(message).to_event(SampleRecorded)

    assert rebuilt == event


def test_outbox_envelope_from_message_copies_routing_fields() -> None:
    event = make_event()
    message = OutboxWriter(FixedClock()).to_message(event)

    envelope = OutboxEnvelope.from_message(message)

    assert envelope.event_id == event.event_id
    assert envelope.event_type == SampleRecorded.event_type
    assert envelope.aggregate_type == "sample"
    assert envelope.aggregate_id == event.aggregate_id
    assert envelope.occurred_at == event.occurred_at


def test_outbox_envelope_to_event_other_event_class_raises_validation_error() -> None:
    message = OutboxWriter(FixedClock()).to_message(make_event())
    envelope = OutboxEnvelope.from_message(message)

    with pytest.raises(ValidationError, match=r"testing\.other_recorded"):
        envelope.to_event(OtherRecorded)


def test_outbox_envelope_to_event_payload_missing_field_raises_pydantic_error() -> None:
    message = OutboxWriter(FixedClock()).to_message(make_event())
    message.payload = {
        key: value for key, value in message.payload.items() if key != "note"
    }
    envelope = OutboxEnvelope.from_message(message)

    with pytest.raises(pydantic.ValidationError, match="note"):
        envelope.to_event(SampleRecorded)


def test_outbox_envelope_is_frozen() -> None:
    envelope = OutboxEnvelope.from_message(
        OutboxWriter(FixedClock()).to_message(make_event())
    )

    with pytest.raises(pydantic.ValidationError, match="frozen"):
        envelope.event_type = "testing.changed"  # type: ignore[misc]  # reason: asserting the frozen model rejects assignment
