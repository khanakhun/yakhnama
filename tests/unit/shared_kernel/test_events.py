"""Unit tests for ``yakhnama.shared_kernel.events``."""

from datetime import UTC, datetime, timedelta, timezone
from typing import ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.events import RecordingEventRecorder
from yakhnama.shared_kernel.events import (
    EVENT_TYPE_MAX_LENGTH,
    AggregateChange,
    DomainEvent,
    EventRecorder,
    is_event_type,
)
from yakhnama.shared_kernel.ids import Uuid7Generator

ids = Uuid7Generator()
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class HazardTypeRetired(DomainEvent):
    """Example event.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "hazards.hazard_type_retired"

    reason: str


class _HazardType(BaseModel):
    """Example aggregate.

    Implements: Aggregate Root.
    """

    model_config = ConfigDict(frozen=True)

    is_retired: bool


def _retired(**overrides: object) -> HazardTypeRetired:
    fields: dict[str, object] = {
        "event_id": ids.new_id(),
        "occurred_at": NOW,
        "aggregate_id": ids.new_id(),
        "aggregate_type": "hazard_type",
        "reason": "merged",
    }
    fields.update(overrides)
    return HazardTypeRetired.model_validate(fields)


def test_domain_event_valid_fields_builds_frozen_event() -> None:
    event = _retired()

    with pytest.raises(PydanticValidationError):
        event.reason = "changed"  # type: ignore[misc]  # reason: proves the frozen model rejects assignment

    assert event.event_type == "hazards.hazard_type_retired"


def test_domain_event_offset_occurred_at_is_normalised_to_utc() -> None:
    karachi = timezone(timedelta(hours=5))

    event = _retired(occurred_at=datetime(2026, 9, 23, 17, 0, tzinfo=karachi))

    assert event.occurred_at == NOW
    assert event.occurred_at.tzinfo is UTC


def test_domain_event_naive_occurred_at_raises_validation_error() -> None:
    naive = NOW.replace(tzinfo=None)

    with pytest.raises(PydanticValidationError):
        _retired(occurred_at=naive)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aggregate_type", "HazardType"),
        ("aggregate_type", ""),
        ("aggregate_id", "0b0a3e6e-0000-4000-8000-000000000000"),
        ("event_id", "0b0a3e6e-0000-4000-8000-000000000000"),
    ],
)
def test_domain_event_malformed_field_raises_validation_error(
    field: str, value: object
) -> None:
    with pytest.raises(PydanticValidationError):
        _retired(**{field: value})


def test_domain_event_occurred_at_outside_utc_range_raises_validation_error() -> None:
    east = timezone(timedelta(hours=5))
    earliest_local = datetime.min.replace(tzinfo=east)

    with pytest.raises(PydanticValidationError, match="representable"):
        _retired(occurred_at=earliest_local)


def test_domain_event_json_round_trip_returns_equal_event() -> None:
    event = _retired()

    restored = HazardTypeRetired.model_validate_json(event.model_dump_json())

    assert restored == event


def test_domain_event_without_event_type_raises_type_error() -> None:
    class _Untyped(DomainEvent):
        """Event that forgot its type.

        Implements: Domain Events.
        """

    with pytest.raises(TypeError, match="does not declare an event_type"):
        _Untyped(
            event_id=ids.new_id(),
            occurred_at=NOW,
            aggregate_id=ids.new_id(),
            aggregate_type="thing",
        )


def test_domain_event_malformed_event_type_raises_type_error_at_definition() -> None:
    with pytest.raises(TypeError, match="must match"):

        class _Malformed(DomainEvent):
            """Event with a non-conforming type.

            Implements: Domain Events.
            """

            event_type: ClassVar[str] = "HazardTypeRetired"


@given(
    context=st.from_regex(r"\A[a-z][a-z0-9_]{0,20}\Z"),
    name=st.from_regex(r"\A[a-z][a-z0-9_]{0,20}\Z"),
)
def test_is_event_type_well_formed_names_return_true(context: str, name: str) -> None:
    result = is_event_type(f"{context}.{name}")

    assert result is True


@pytest.mark.parametrize(
    "value",
    [
        "hazards",
        "Hazards.retired",
        "hazards.retired.again",
        "hazards.",
        "a." + "b" * EVENT_TYPE_MAX_LENGTH,
        42,
        None,
    ],
)
def test_is_event_type_malformed_values_return_false(value: object) -> None:
    result = is_event_type(value)

    assert result is False


def test_aggregate_change_record_into_records_events_in_order_and_returns_state() -> (
    None
):
    first, second = _retired(reason="first"), _retired(reason="second")
    change = AggregateChange[_HazardType](
        state=_HazardType(is_retired=True), events=(first, second)
    )
    recorder: EventRecorder = RecordingEventRecorder()

    state = change.record_into(recorder)

    assert state == _HazardType(is_retired=True)
    assert isinstance(recorder, RecordingEventRecorder)
    assert recorder.events == [first, second]


def test_aggregate_change_without_events_records_nothing() -> None:
    change = AggregateChange[_HazardType](state=_HazardType(is_retired=False))
    recorder = RecordingEventRecorder()

    change.record_into(recorder)

    assert recorder.events == []
    assert change.events == ()
