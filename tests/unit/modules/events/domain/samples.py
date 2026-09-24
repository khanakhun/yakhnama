"""Deterministic clock, ids and sample geometries for the events domain tests.

Patterns: Fake.
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from geojson_pydantic import MultiPolygon, Polygon
from geojson_pydantic.types import Position, Position2D

from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.events.domain.value_objects import EventGeometry
from yakhnama.shared_kernel.value_objects import DatePrecision, DateWithPrecision

NOW: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
MODERATOR_ID: Final = SequentialIdGenerator(seed=31).new_id()
REASON: Final = "Duplicate of an event recorded from the district bulletin."


def stepping_clock(start: datetime = NOW) -> SteppingClock:
    """Return a clock starting at ``start`` and advancing one minute per call.

    Args:
        start: The first instant returned.

    Returns:
        The clock.
    """
    return SteppingClock(start, timedelta(minutes=1))


def ids(seed: int = 0) -> SequentialIdGenerator:
    """Return a deterministic id source.

    Args:
        seed: Selects the random bits.

    Returns:
        The generator.
    """
    return SequentialIdGenerator(seed=seed)


def at(
    year: int,
    month: int,
    day: int,
    precision: DatePrecision = DatePrecision.DAY,
    hour: int = 0,
) -> DateWithPrecision:
    """Return a UTC instant with a precision.

    Args:
        year: Year.
        month: Month.
        day: Day.
        precision: How precisely it is known.
        hour: Hour.

    Returns:
        The instant.
    """
    return DateWithPrecision(
        value=datetime(year, month, day, hour, tzinfo=UTC), precision=precision
    )


def ring(*corners: tuple[float, float]) -> list[Position]:
    """Return a closed GeoJSON ring through ``corners``.

    Args:
        corners: Longitude and latitude pairs, not closed.

    Returns:
        The positions with the first repeated at the end.
    """
    positions: list[Position] = [
        Position2D(longitude=longitude, latitude=latitude)
        for longitude, latitude in corners
    ]
    return [*positions, positions[0]]


def square(west: float, south: float, size: float) -> Polygon:
    """Return an axis-aligned square polygon.

    Args:
        west: Western edge.
        south: Southern edge.
        size: Edge length in degrees.

    Returns:
        The polygon.
    """
    return Polygon(
        type="Polygon",
        coordinates=[
            ring(
                (west, south),
                (west + size, south),
                (west + size, south + size),
                (west, south + size),
            )
        ],
    )


def square_geometry(west: float = 74.0, south: float = 36.0) -> EventGeometry:
    """Return a 0.2-degree square event geometry.

    Args:
        west: Western edge.
        south: Southern edge.

    Returns:
        The geometry; its centroid is the square's centre.
    """
    return EventGeometry(geojson=square(west, south, 0.2))


def two_squares() -> MultiPolygon:
    """Return a multipolygon of two unit squares side by side.

    Returns:
        The multipolygon.
    """
    return MultiPolygon(
        type="MultiPolygon",
        coordinates=[
            square(74.0, 36.0, 1.0).coordinates,
            square(76.0, 36.0, 1.0).coordinates,
        ],
    )
