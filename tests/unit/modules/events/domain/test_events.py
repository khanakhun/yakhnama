"""Unit tests for ``yakhnama.modules.events.domain.events``."""

import pytest

from yakhnama.modules.events.domain.events import (
    AffectedPlaceAdded,
    EventAttributesChanged,
    EventCreated,
    EventGeometryChanged,
    EventMerged,
    EventPeriodChanged,
    EventPublished,
    EventRecordEvent,
    EventRetracted,
    EventsRelated,
    ReportLinkedToEvent,
    ReportUnlinkedFromEvent,
)

EXPECTED = [
    (EventCreated, "events.event_created"),
    (ReportLinkedToEvent, "events.report_linked_to_event"),
    (ReportUnlinkedFromEvent, "events.report_unlinked_from_event"),
    (AffectedPlaceAdded, "events.affected_place_added"),
    (EventGeometryChanged, "events.event_geometry_changed"),
    (EventPeriodChanged, "events.event_period_changed"),
    (EventAttributesChanged, "events.event_attributes_changed"),
    (EventPublished, "events.event_published"),
    (EventRetracted, "events.event_retracted"),
    (EventMerged, "events.event_merged"),
    (EventsRelated, "events.events_related"),
]


@pytest.mark.parametrize(("event_class", "event_type"), EXPECTED)
def test_event_record_event_classes_declare_stable_event_type(
    event_class: type[EventRecordEvent], event_type: str
) -> None:
    declared = event_class.event_type

    assert declared == event_type


@pytest.mark.parametrize(("event_class", "_event_type"), EXPECTED)
def test_event_record_event_payloads_carry_no_free_text(
    event_class: type[EventRecordEvent], _event_type: str
) -> None:
    fields = set(event_class.model_fields)

    assert not fields & {"reason", "note", "title", "summary", "status_reason"}
    assert "actor_id" in fields
