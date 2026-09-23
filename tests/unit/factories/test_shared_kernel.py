"""Unit tests for ``tests.factories.shared_kernel``."""

from datetime import UTC

import pytest
from pydantic import BaseModel

from tests.factories.shared_kernel import (
    TEST_REGION_ENVELOPE,
    BoundingBoxFactory,
    CoordinatesFactory,
    DateWithPrecisionFactory,
    LocalizedTextFactory,
    MeasurementFactory,
    english_text,
)
from yakhnama.shared_kernel.value_objects import (
    KNOWN_UNITS,
    Coordinates,
    SiUnit,
)

BUILDS = 50


def _revalidates[ModelT: BaseModel](instance: ModelT) -> bool:
    # Building through the model already validated it; a round trip through the
    # serialised form proves the value also survives storage and the API.
    return type(instance).model_validate(instance.model_dump()) == instance


def test_coordinates_factory_builds_valid_points_in_the_test_region() -> None:
    points = [CoordinatesFactory.build() for _ in range(BUILDS)]

    assert all(_revalidates(point) for point in points)
    assert all(TEST_REGION_ENVELOPE.contains(point) for point in points)


def test_bounding_box_factory_builds_consistent_boxes_in_the_test_region() -> None:
    boxes = [BoundingBoxFactory.build() for _ in range(BUILDS)]

    assert all(_revalidates(box) for box in boxes)
    assert all(box.min_longitude <= box.max_longitude for box in boxes)
    assert all(box.min_latitude <= box.max_latitude for box in boxes)
    assert all(
        TEST_REGION_ENVELOPE.contains(
            Coordinates(longitude=box.max_longitude, latitude=box.max_latitude)
        )
        for box in boxes
    )


def test_bounding_box_factory_minimum_override_keeps_the_box_consistent() -> None:
    box = BoundingBoxFactory.build(min_longitude=78.5, min_latitude=10)

    assert box.max_longitude >= 78.5
    assert box.max_latitude >= 10


def test_bounding_box_factory_non_numeric_minimum_raises_type_error() -> None:
    with pytest.raises(TypeError, match="expected a number"):
        BoundingBoxFactory.build(min_longitude="west")


def test_measurement_factory_builds_non_negative_values_in_known_units() -> None:
    measurements = [MeasurementFactory.build() for _ in range(BUILDS)]

    assert all(_revalidates(measurement) for measurement in measurements)
    assert all(measurement.unit in KNOWN_UNITS for measurement in measurements)
    assert all(measurement.value >= 0 for measurement in measurements)


def test_measurement_factory_count_unit_gives_whole_values() -> None:
    counts = [MeasurementFactory.build(unit=SiUnit.COUNT.value) for _ in range(BUILDS)]

    assert all(count.value.is_integer() for count in counts)


def test_date_with_precision_factory_builds_utc_instants() -> None:
    dates = [DateWithPrecisionFactory.build() for _ in range(BUILDS)]

    assert all(_revalidates(date) for date in dates)
    assert all(date.value.tzinfo is UTC for date in dates)


def test_localized_text_factory_builds_unique_english_texts() -> None:
    texts = [LocalizedTextFactory.build() for _ in range(BUILDS)]

    assert all(_revalidates(text) for text in texts)
    assert all(text.get("en") is not None for text in texts)
    assert len({text.get("en") for text in texts}) == BUILDS


def test_english_text_returns_a_new_english_entry_per_call() -> None:
    first, second = english_text(), english_text()

    assert set(first) == set(second) == {"en"}
    assert first != second
