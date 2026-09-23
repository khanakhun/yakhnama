"""Factories for the shared-kernel value objects.

Patterns: Factory.
"""

from collections.abc import Mapping

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
    uniform,
)
from yakhnama.shared_kernel.value_objects import (
    KNOWN_UNITS,
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    LocalizedText,
    Measurement,
    SiUnit,
)

# Test data, not a boundary fact: an approximate lon/lat envelope around
# Gilgit-Baltistan, the same rectangle the geography domain tests use. The sourced
# boundary arrives with the reference data; nothing may treat this box as authoritative.
TEST_REGION_MIN_LONGITUDE = 72.0
TEST_REGION_MAX_LONGITUDE = 77.9
TEST_REGION_MIN_LATITUDE = 34.5
TEST_REGION_MAX_LATITUDE = 37.1

TEST_REGION_ENVELOPE = BoundingBox(
    min_longitude=TEST_REGION_MIN_LONGITUDE,
    min_latitude=TEST_REGION_MIN_LATITUDE,
    max_longitude=TEST_REGION_MAX_LONGITUDE,
    max_latitude=TEST_REGION_MAX_LATITUDE,
)
"""Every ``CoordinatesFactory`` point and ``BoundingBoxFactory`` box lies inside it."""

MEASUREMENT_MAX_VALUE = 1_000_000.0
"""Upper bound of generated measurement values; large enough, never overflowing."""

_next_text = sequence("Test text {}")


def _upper_edge(minimum: object, region_maximum: float) -> float:
    # The minimum may come from the caller rather than the factory, so it is checked
    # and may lie outside the test region; the edge never falls below it.
    if not isinstance(minimum, int | float):
        message = f"expected a number for the minimum edge, got {minimum!r}"
        raise TypeError(message)
    return uniform(float(minimum), max(float(minimum), region_maximum))


def _max_longitude(_name: str, values: Mapping[str, object]) -> float:
    return _upper_edge(values["min_longitude"], TEST_REGION_MAX_LONGITUDE)


def _max_latitude(_name: str, values: Mapping[str, object]) -> float:
    return _upper_edge(values["min_latitude"], TEST_REGION_MAX_LATITUDE)


def _value_for_unit(_name: str, values: Mapping[str, object]) -> float:
    # A count is a whole number of things, so the value is integral even though the
    # field is a float.
    if values["unit"] == SiUnit.COUNT.value:
        return float(round(uniform(0.0, MEASUREMENT_MAX_VALUE)))
    return uniform(0.0, MEASUREMENT_MAX_VALUE)


def english_text() -> dict[str, str]:
    """Return a fresh, unique English-only ``texts`` mapping.

    Returns:
        ``{"en": "Test text <n>"}`` with a new ``n`` per call.
    """
    return {"en": _next_text()}


class CoordinatesFactory(YakhnamaModelFactory[Coordinates]):
    """Builds points inside ``TEST_REGION_ENVELOPE``.

    Implements: Factory.
    """

    __model__ = Coordinates

    longitude = Use(uniform, TEST_REGION_MIN_LONGITUDE, TEST_REGION_MAX_LONGITUDE)
    latitude = Use(uniform, TEST_REGION_MIN_LATITUDE, TEST_REGION_MAX_LATITUDE)


class BoundingBoxFactory(YakhnamaModelFactory[BoundingBox]):
    """Builds boxes inside ``TEST_REGION_ENVELOPE`` with ``min <= max`` on both axes.

    The maxima are drawn after the minima, so overriding a minimum still gives a
    consistent box; overriding only a maximum may not.

    Implements: Factory.
    """

    __model__ = BoundingBox

    min_longitude = Use(uniform, TEST_REGION_MIN_LONGITUDE, TEST_REGION_MAX_LONGITUDE)
    min_latitude = Use(uniform, TEST_REGION_MIN_LATITUDE, TEST_REGION_MAX_LATITUDE)
    max_longitude = PostGenerated(_max_longitude)
    max_latitude = PostGenerated(_max_latitude)


class MeasurementFactory(YakhnamaModelFactory[Measurement]):
    """Builds non-negative measurements in a registered unit.

    Counts get whole values. Pass ``unit=`` to fix the unit; the value follows it.

    Implements: Factory.
    """

    __model__ = Measurement

    unit = pick(sorted(KNOWN_UNITS))
    value = PostGenerated(_value_for_unit)


class DateWithPrecisionFactory(YakhnamaModelFactory[DateWithPrecision]):
    """Builds UTC instants between 2000 and 2026 with a random precision.

    Implements: Factory.
    """

    __model__ = DateWithPrecision

    value = Use(random_instant)
    precision = pick(list(DatePrecision))


class LocalizedTextFactory(YakhnamaModelFactory[LocalizedText]):
    """Builds English-only texts, unique per build.

    Implements: Factory.
    """

    __model__ = LocalizedText

    texts = Use(english_text)
