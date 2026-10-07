"""Builders for district boundary test data: synthetic squares, links and sources.

Every district here is synthetic (``XX1`` region codes, "Synthetic" names, squares
far from Gilgit-Baltistan), never a real boundary. ``grid_boundary_set`` builds a
``columns`` by ``rows`` grid of unit squares that share edges exactly, so its
shared edges and outer outline are known in advance.

Patterns: Factory.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Final

from geojson_pydantic import LineString, Polygon
from geojson_pydantic.types import Position, Position2D

from yakhnama.modules.geography.domain.boundaries import (
    BoundaryAttribution,
    DistrictBoundarySet,
    DistrictBoundarySource,
    DistrictLink,
    EdgeGeometry,
    SourceDistrict,
)
from yakhnama.modules.geography.domain.value_objects import PlaceGeometry
from yakhnama.shared_kernel.value_objects import Coordinates

RETRIEVED_AT: Final = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SYNTHETIC_SHA256: Final = "a" * 64
SYNTHETIC_REGION: Final = "XX1"
GRID_ORIGIN: Final = (10.0, 20.0)


def attribution(retrieved_at: datetime = RETRIEVED_AT) -> BoundaryAttribution:
    """Return a synthetic attribution.

    Args:
        retrieved_at: The download time.

    Returns:
        The attribution.
    """
    return BoundaryAttribution(
        source="Synthetic boundaries for tests",
        source_url="https://example.org/synthetic",
        licence="CC BY 4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        dataset_version="synthetic v1",
        retrieved_at=retrieved_at,
    )


def square(west: float, south: float, east: float, north: float) -> PlaceGeometry:
    """Return an axis-aligned rectangle as a polygon footprint.

    Args:
        west: Minimum longitude.
        south: Minimum latitude.
        east: Maximum longitude.
        north: Maximum latitude.

    Returns:
        A closed counter-clockwise ring.
    """
    ring: list[Position] = [
        Position2D(longitude=west, latitude=south),
        Position2D(longitude=east, latitude=south),
        Position2D(longitude=east, latitude=north),
        Position2D(longitude=west, latitude=north),
        Position2D(longitude=west, latitude=south),
    ]
    return PlaceGeometry(geojson=Polygon(type="Polygon", coordinates=[ring]))


def grid_code(column: int, row: int, columns: int) -> str:
    """Return the synthetic code of a grid cell, numbered row by row from 1.

    Args:
        column: Zero-based column.
        row: Zero-based row.
        columns: Columns per row.

    Returns:
        ``XX1`` followed by a two-digit number, for example ``XX101``.
    """
    return f"{SYNTHETIC_REGION}{row * columns + column + 1:02d}"


def grid_boundary_set(
    columns: int = 3, rows: int = 2, *, sha256: str = SYNTHETIC_SHA256
) -> DistrictBoundarySet:
    """Return a grid of unit squares sharing edges exactly.

    Args:
        columns: Squares per row.
        rows: Rows of squares.
        sha256: The digest the set claims to come from.

    Returns:
        The boundary set, codes from ``grid_code``.
    """
    west, south = GRID_ORIGIN
    districts = tuple(
        SourceDistrict(
            code=grid_code(column, row, columns),
            name=f"Synthetic {grid_code(column, row, columns)}",
            geometry=square(
                west + column, south + row, west + column + 1, south + row + 1
            ),
            representative_point=Coordinates(
                longitude=west + column + 0.5, latitude=south + row + 0.5
            ),
        )
        for row in range(rows)
        for column in range(columns)
    )
    return DistrictBoundarySet(
        attribution=attribution(),
        sha256=sha256,
        region_code=SYNTHETIC_REGION,
        districts=districts,
    )


def boundary_source(
    links: Iterable[tuple[str, str | None]],
    *,
    sha256: str = SYNTHETIC_SHA256,
    region_place_code: str = "pk.gb",
) -> DistrictBoundarySource:
    """Return a synthetic boundary source with the given links.

    Each link's ``source_name`` is ``Synthetic <code>``, matching
    ``grid_boundary_set``.

    Args:
        links: ``(source_code, place_code)`` pairs.
        sha256: The pinned digest.
        region_place_code: The gazetteer region.

    Returns:
        The source.
    """
    return DistrictBoundarySource(
        schema_version=1,
        data_version="2026-10-05",
        dataset="synthetic-boundaries",
        download_url="https://example.org/synthetic.zip",
        sha256=sha256,
        archive_member="synthetic_admin2.geojson",
        region_code=SYNTHETIC_REGION,
        region_place_code=region_place_code,
        source="Synthetic boundaries for tests",
        source_url="https://example.org/synthetic",
        licence="CC BY 4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        dataset_version="synthetic v1",
        links=tuple(
            DistrictLink(
                source_code=code, source_name=f"Synthetic {code}", place_code=place
            )
            for code, place in links
        ),
    )


def line(*positions: tuple[float, float]) -> EdgeGeometry:
    """Return a LineString edge geometry through ``positions``.

    Args:
        positions: At least two ``(longitude, latitude)`` pairs.

    Returns:
        The edge geometry.
    """
    return EdgeGeometry(
        geojson=LineString(
            type="LineString",
            coordinates=[
                Position2D(longitude=longitude, latitude=latitude)
                for longitude, latitude in positions
            ],
        )
    )
