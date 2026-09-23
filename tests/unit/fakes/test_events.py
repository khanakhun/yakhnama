"""Unit tests for ``tests.fakes.events``."""

from datetime import UTC, datetime
from typing import ClassVar, get_protocol_members

from tests.fakes.events import RecordingEventRecorder
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.shared_kernel.events import DomainEvent, EventRecorder

ids = SequentialIdGenerator()
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class _ThingCreated(DomainEvent):
    """Example event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "tests.thing_created"


class _ThingRetired(DomainEvent):
    """Another example event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "tests.thing_retired"


def _event[EventT: DomainEvent](event_class: type[EventT]) -> EventT:
    return event_class(
        event_id=ids.new_id(),
        occurred_at=NOW,
        aggregate_id=ids.new_id(),
        aggregate_type="thing",
    )


def test_recording_event_recorder_declares_every_protocol_member() -> None:
    members = get_protocol_members(EventRecorder)

    missing = {m for m in members if not hasattr(RecordingEventRecorder, m)}

    assert missing == set()


def test_recording_event_recorder_keeps_events_in_order() -> None:
    created, retired = _event(_ThingCreated), _event(_ThingRetired)
    recorder: EventRecorder = RecordingEventRecorder()

    recorder.record_event(created)
    recorder.record_event(retired)

    assert isinstance(recorder, RecordingEventRecorder)
    assert recorder.events == [created, retired]


def test_recording_event_recorder_events_of_type_filters_by_class() -> None:
    created, retired = _event(_ThingCreated), _event(_ThingRetired)
    recorder = RecordingEventRecorder()
    recorder.record_event(created)
    recorder.record_event(retired)

    only_retired = recorder.events_of_type(_ThingRetired)

    assert only_retired == (retired,)
    assert recorder.events_of_type(DomainEvent) == (created, retired)
