"""Compute the edges the districts of one region share, with Shapely.

For every pair of districts whose polygons come within ``snap_tolerance`` of each
other, the second polygon's vertices are snapped to the first's
(``shapely.snap``), and the shared edge is the linework the two outlines then have
in common, merged into as few lines as possible. Snapping keeps the edge whole when
the dataset's shared vertices differ by a few centimetres; with a clean coverage
(COD-AB for Pakistan is one) it changes nothing.

Each edge is then simplified with Douglas-Peucker (``simplify`` with
``preserve_topology``) at ``simplify_tolerance``. Douglas-Peucker never moves a
line's end points, so edges still meet where three districts meet.

**The outer edge is removed last.** The region's outline is the boundary of the
union of all its districts (closed over gaps narrower than the snap tolerance with
mitre joins, so every corner of the outline, notches included, stays exactly where
the data has it).
Everything within ``outer_clearance`` of it is cut away *after* simplification, so
no returned line lies on, or comes closer than the clearance to, the region's
outline: an international border or the Line of Control can never be drawn from
these lines (maintainer decision, 2026-10-05; ADR 0021). The cut leaves each edge
ending about ``outer_clearance`` short of the outline.

Coordinates are finally rounded to ``decimals`` decimal places (5, about 1 m) to keep
the published payload small, and parts shorter than ``min_part_length`` (slivers
left by the cut) are dropped.

**Coverage check.** ``shapely.coverage_invalid_edges`` (GEOS ``CoverageValidator``)
names every district whose outline overlaps a neighbour, fails to share its
vertices, or leaves a gap narrower than ``coverage_gap_width`` (by default the
outline clearance). Such a gap is a digitising error: snapping cannot close it, so
the edge across it is lost, and the outline then runs through the gap. The load
refuses to publish an invalid coverage unless the operator allows it (ADR 0021).

The work is CPU-bound and synchronous; the use case runs ``compute`` in a worker
thread. The defaults are **proposed** (ADR 0021): 1e-5° snap (~1 m), 5e-4°
simplification (~50 m), 5e-4° clearance and gap width, 1e-3° shortest part
(~100 m), 5 decimals.

Patterns: Adapter.
"""

import itertools
from typing import Final

import numpy
import numpy.typing
import shapely
from shapely.geometry import LineString, MultiLineString, mapping, shape
from shapely.geometry.base import BaseGeometry

from yakhnama.modules.geography.application.dto import SharedEdgeComputation
from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySet,
    EdgeGeometry,
    SharedEdge,
)

SNAP_TOLERANCE_DEGREES: Final = 1e-5
SIMPLIFY_TOLERANCE_DEGREES: Final = 5e-4
OUTER_CLEARANCE_DEGREES: Final = 5e-4
MIN_PART_LENGTH_DEGREES: Final = 1e-3
COORDINATE_DECIMALS: Final = 5
MITRE_LIMIT: Final = 10_000.0
"""How far a mitre join may reach, in multiples of the offset; high enough that no
real corner is bevelled, so the closing gives every corner back unchanged."""


class ShapelySharedEdgeCalculator:
    """``SharedEdgeCalculator`` over Shapely (GEOS).

    Implements: Adapter.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per tunable distance, all defaulted
        self,
        *,
        snap_tolerance: float = SNAP_TOLERANCE_DEGREES,
        simplify_tolerance: float = SIMPLIFY_TOLERANCE_DEGREES,
        outer_clearance: float = OUTER_CLEARANCE_DEGREES,
        min_part_length: float = MIN_PART_LENGTH_DEGREES,
        decimals: int = COORDINATE_DECIMALS,
        coverage_gap_width: float | None = None,
    ) -> None:
        """Create the calculator; every distance is in degrees.

        Args:
            snap_tolerance: How far apart two outlines may be and still share an
                edge.
            simplify_tolerance: Douglas-Peucker tolerance.
            outer_clearance: How far every returned line stays from the region's
                outline; more than ``snap_tolerance`` plus the rounding step.
            min_part_length: Shorter line parts are dropped.
            decimals: Coordinates are rounded to this many decimal places, 1 to 15.
            coverage_gap_width: Gaps between districts narrower than this make the
                coverage invalid; ``None`` uses ``outer_clearance``, the width below
                which a gap would cut a shared edge away.

        Raises:
            ValueError: If a distance is not positive, ``decimals`` is out of range,
                the clearance does not exceed the snap tolerance plus the rounding
                step, or ``coverage_gap_width`` is negative.
        """
        distances = (
            snap_tolerance,
            simplify_tolerance,
            outer_clearance,
            min_part_length,
        )
        if min(distances) <= 0:
            message = "every distance must be positive"
            raise ValueError(message)
        if not 1 <= decimals <= 15:  # noqa: PLR2004  # reason: float64 precision
            message = "decimals must be between 1 and 15"
            raise ValueError(message)
        if outer_clearance <= snap_tolerance + 10.0**-decimals:
            message = (
                "outer_clearance must exceed snap_tolerance plus the rounding step"
            )
            raise ValueError(message)
        if coverage_gap_width is not None and coverage_gap_width < 0:
            message = "coverage_gap_width must not be negative"
            raise ValueError(message)
        self._gap_width = (
            outer_clearance if coverage_gap_width is None else coverage_gap_width
        )
        self._snap = snap_tolerance
        self._simplify = simplify_tolerance
        self._clearance = outer_clearance
        self._min_length = min_part_length
        self._decimals = decimals

    def compute(self, boundary_set: DistrictBoundarySet) -> SharedEdgeComputation:
        """Return the shared edges of every pair of adjacent districts.

        Args:
            boundary_set: The region's districts.

        Returns:
            The edges sorted by pair, whether the polygons formed a valid coverage
            (and which districts break it), and how many short parts were dropped.
        """
        polygons = {
            district.code: shape(district.geometry.geojson.model_dump(mode="json"))
            for district in boundary_set.districts
        }
        invalid = shapely.coverage_invalid_edges(
            list(polygons.values()), gap_width=self._gap_width
        )
        invalid_districts = tuple(
            sorted(
                code
                for code, edges in zip(polygons, invalid.tolist(), strict=True)
                if edges is not None and not edges.is_empty
            )
        )
        # Closing with mitre joins (offset every side out, then back) fills gaps
        # narrower than the snap tolerance but restores every corner exactly; a
        # round join would pull the outline back from the tip of a sharp notch,
        # and the clearance would then fall short of it there.
        grown = [
            polygon.buffer(self._snap, join_style="mitre", mitre_limit=MITRE_LIMIT)
            for polygon in polygons.values()
        ]
        outline = (
            shapely.union_all(grown)
            .buffer(-self._snap, join_style="mitre", mitre_limit=MITRE_LIMIT)
            .boundary
        )
        outer_zone = outline.buffer(self._clearance)
        edges: list[SharedEdge] = []
        dropped = 0
        for first, second in itertools.combinations(sorted(polygons), 2):
            if not shapely.dwithin(polygons[first], polygons[second], self._snap):
                continue
            aligned = shapely.snap(polygons[second], polygons[first], self._snap)
            shared = polygons[first].boundary.intersection(aligned.boundary)
            parts, short = self._finish(shared, outer_zone)
            dropped += short
            if parts:
                edges.append(_shared_edge(first, second, parts))
        return SharedEdgeComputation(
            edges=tuple(edges),
            is_coverage_valid=not invalid_districts,
            invalid_coverage_districts=invalid_districts,
            dropped_parts=dropped,
        )

    def _finish(
        self, shared: BaseGeometry, outer_zone: BaseGeometry
    ) -> tuple[list[LineString], int]:
        lines = _line_parts(shared)
        if not lines:
            return [], 0
        merged = shapely.line_merge(shapely.union_all(lines))
        simplified = merged.simplify(self._simplify, preserve_topology=True)
        inside = simplified.difference(outer_zone)
        rounded = shapely.transform(inside, self._round)
        candidates = _line_parts(shapely.line_merge(rounded))
        kept = [line for line in candidates if line.length >= self._min_length]
        return kept, len(candidates) - len(kept)

    def _round(
        self, coordinates: numpy.typing.NDArray[numpy.float64]
    ) -> numpy.typing.NDArray[numpy.float64]:
        return numpy.round(coordinates, self._decimals)


def _line_parts(geometry: BaseGeometry) -> list[LineString]:
    # Intersections return points, lines and (possibly nested) collections of both;
    # only non-empty lines count.
    lines: list[LineString] = []
    for part in shapely.get_parts(geometry).tolist():
        if isinstance(part, LineString):
            if not part.is_empty:
                lines.append(part)
        elif part.geom_type in {"MultiLineString", "GeometryCollection"}:
            lines.extend(_line_parts(part))
    return lines


def _shared_edge(first: str, second: str, parts: list[LineString]) -> SharedEdge:
    line: BaseGeometry = parts[0] if len(parts) == 1 else MultiLineString(parts)
    return SharedEdge(
        source_codes=(first, second),
        geometry=EdgeGeometry.model_validate({"geojson": mapping(line)}),
    )
