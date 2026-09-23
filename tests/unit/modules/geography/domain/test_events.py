"""Unit tests for ``yakhnama.modules.geography.domain.events``."""

import re
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.geography.domain.events import (
    PlaceCentroidChanged,
    PlaceCreated,
    PlaceEvent,
    PlaceGeometryChanged,
    PlaceMerged,
    PlaceNameAdded,
    PlacePreferredNameChanged,
    PlaceRetired,
)
from yakhnama.modules.geography.domain.value_objects import AdminLevel, PlaceName
from yakhnama.shared_kernel.ids import Uuid7Generator

ids = Uuid7Generator()
NOW = datetime(2026, 9, 23, tzinfo=UTC)
EVENT_CLASSES = [
    PlaceCreated,
    PlaceNameAdded,
    PlacePreferredNameChanged,
    PlaceGeometryChanged,
    PlaceCentroidChanged,
    PlaceRetired,
    PlaceMerged,
]


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


@pytest.mark.parametrize("event_class", EVENT_CLASSES, ids=lambda cls: cls.__name__)
def test_place_event_type_is_geography_dot_snake_case_class_name(
    event_class: type[PlaceEvent],
) -> None:
    expected = f"geography.{_snake_case(event_class.__name__)}"

    event_type = event_class.event_type

    assert event_type == expected


def _base_fields() -> dict[str, object]:
    return {
        "event_id": ids.new_id(),
        "occurred_at": NOW,
        "aggregate_id": ids.new_id(),
        "place_code": "pk.gb.hunza",
        "version": 1,
    }


def test_place_event_base_without_event_type_raises_type_error() -> None:
    with pytest.raises(TypeError, match="event_type"):
        PlaceEvent.model_validate(_base_fields())


def test_place_event_aggregate_type_defaults_to_place() -> None:
    event = PlaceRetired.model_validate({**_base_fields(), "reason": "abolished"})

    assert event.aggregate_type == "place"


def test_place_event_other_aggregate_type_raises_validation_error() -> None:
    fields = {**_base_fields(), "reason": "abolished", "aggregate_type": "hazard_type"}

    with pytest.raises(PydanticValidationError):
        PlaceRetired.model_validate(fields)


def test_place_event_version_zero_raises_validation_error() -> None:
    fields = {**_base_fields(), "reason": "abolished", "version": 0}

    with pytest.raises(PydanticValidationError):
        PlaceRetired.model_validate(fields)


def test_place_created_round_trip_through_json_returns_equal_event() -> None:
    event = PlaceCreated.model_validate(
        {
            **_base_fields(),
            "level": AdminLevel.COUNTRY,
            "parent_id": None,
            "names": (PlaceName(text="Pakistan", language="en"),),
            "geometry_type": None,
            "centroid": None,
        }
    )

    restored = PlaceCreated.model_validate_json(event.model_dump_json())

    assert restored == event
