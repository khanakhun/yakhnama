"""Unit tests for ``yakhnama.platform.outbox.writer`` and the outbox table model."""

from datetime import timedelta

from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from tests.unit.platform.events import FIXED_INSTANT, FixedClock, make_event
from tests.unit.platform.sessions import RecordingSession
from yakhnama.platform.db import Base
from yakhnama.platform.outbox.models import OUTBOX_TABLE_NAME, OutboxMessage
from yakhnama.platform.outbox.writer import OutboxWriter


def test_outbox_writer_to_message_serialises_event_exactly() -> None:
    clock = FixedClock(FIXED_INSTANT + timedelta(seconds=5))
    event = make_event("glacier lake rising")

    message = OutboxWriter(clock).to_message(event)

    assert message.id == event.event_id
    assert message.aggregate_type == "sample"
    assert message.aggregate_id == event.aggregate_id
    assert message.event_type == "testing.sample_recorded"
    assert message.payload == {
        "event_id": str(event.event_id),
        "occurred_at": "2026-09-23T08:30:00Z",
        "aggregate_id": str(event.aggregate_id),
        "aggregate_type": "sample",
        "note": "glacier lake rising",
    }
    assert message.occurred_at == FIXED_INSTANT
    assert message.created_at == FIXED_INSTANT + timedelta(seconds=5)
    assert message.published_at is None
    assert message.attempts == 0
    assert message.last_error is None


def test_outbox_writer_write_adds_one_row_per_event_in_order() -> None:
    session = RecordingSession()
    events = [make_event("first"), make_event("second")]

    OutboxWriter(FixedClock()).write(session, events)

    assert [type(row) for row in session.added] == [OutboxMessage, OutboxMessage]
    assert [row.id for row in session.added if isinstance(row, OutboxMessage)] == [
        event.event_id for event in events
    ]
    assert session.calls == []


def test_outbox_writer_write_without_events_adds_nothing() -> None:
    session = RecordingSession()

    OutboxWriter(FixedClock()).write(session, [])

    assert session.added == []


def test_outbox_table_is_registered_on_base_metadata_with_expected_columns() -> None:
    table = Base.metadata.tables[OUTBOX_TABLE_NAME]

    columns = {column.name: column for column in table.columns}

    assert set(columns) == {
        "id",
        "aggregate_type",
        "aggregate_id",
        "event_type",
        "payload",
        "occurred_at",
        "created_at",
        "published_at",
        "attempts",
        "last_error",
    }
    assert isinstance(columns["payload"].type, JSONB)
    assert isinstance(columns["occurred_at"].type, TIMESTAMP)
    assert columns["occurred_at"].type.timezone is True
    assert columns["published_at"].nullable is True
    assert columns["last_error"].nullable is True
    assert columns["attempts"].nullable is False


def test_outbox_table_indexes_published_at_then_created_at() -> None:
    table = Base.metadata.tables[OUTBOX_TABLE_NAME]

    indexes = {
        index.name: [column.name for column in index.columns] for index in table.indexes
    }

    assert indexes == {
        "ix_outbox_messages_published_at_created_at": ["published_at", "created_at"]
    }
