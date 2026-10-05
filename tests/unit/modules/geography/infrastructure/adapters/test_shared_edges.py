"""``ShapelySharedEdgeCalculator``: shared edges only, never the region's outline.

The editorial rule (maintainer decision, 2026-10-05; ADR 0021) is that no returned
line may lie on the outer edge of the region, which traces international borders
and the Line of Control. It is checked on a synthetic grid with a known answer and,
as a property, on random Voronoi coverages clipped to an irregular outer ring.
Every district is synthetic.
"""

import itertools
from typing import Any, Final

import pytest
import shapely
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, MultiPoint, Polygon, mapping, shape
from shapely.geometry.base import BaseGeometry

from tests.factories.boundaries import attribution, grid_boundary_set, square
from yakhnama.modules.geography.application.dto import SharedEdgeComputation
from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySet,
    SharedEdge,
    SourceDistrict,
)
from yakhnama.modules.geography.domain.value_objects import PlaceGeometry
from yakhnama.modules.geography.infrastructure.adapters.shared_edges import (
    COORDINATE_DECIMALS,
    OUTER_CLEARANCE_DEGREES,
    SIMPLIFY_TOLERANCE_DEGREES,
    SNAP_TOLERANCE_DEGREES,
    ShapelySharedEdgeCalculator,
)
from yakhnama.shared_kernel.value_objects import Coordinates

ROUNDING_STEP: Final = 10.0**-COORDINATE_DECIMALS
# A computed distance may fall short of the clearance by the final rounding of each
# coordinate (half a step in each axis) plus floating-point noise.
CLEARANCE_SLACK: Final = ROUNDING_STEP + 1e-9
# Douglas-Peucker keeps original vertices, the cut adds points on the simplified
# line and the rounding moves each by less than one step.
VERTEX_SLACK: Final = (
    SIMPLIFY_TOLERANCE_DEGREES + SNAP_TOLERANCE_DEGREES + ROUNDING_STEP
)
# A non-convex outer ring so that clipping leaves concave cells and multipart ones.
OUTER_RING: Final = Polygon(
    [(0, 0), (1, 0), (1, 0.6), (0.55, 0.45), (0.6, 1), (0, 1), (0.1, 0.5), (0, 0)]
)


def _shape(edge: SharedEdge) -> BaseGeometry:
    return shape(edge.geometry.geojson.model_dump(mode="json"))


def _outline(boundary_set: DistrictBoundarySet) -> BaseGeometry:
    return shapely.union_all(
        [
            shape(district.geometry.geojson.model_dump(mode="json"))
            for district in boundary_set.districts
        ]
    ).boundary


def _boundary_set(polygons: dict[str, BaseGeometry]) -> DistrictBoundarySet:
    return DistrictBoundarySet(
        attribution=attribution(),
        sha256="a" * 64,
        region_code="XX1",
        districts=tuple(
            SourceDistrict(
                code=code,
                name=f"Synthetic {code}",
                geometry=PlaceGeometry.model_validate({"geojson": mapping(polygon)}),
                representative_point=Coordinates(
                    longitude=polygon.representative_point().x,
                    latitude=polygon.representative_point().y,
                ),
            )
            for code, polygon in polygons.items()
        ),
    )


def _assert_off_outline(
    computation: SharedEdgeComputation, boundary_set: DistrictBoundarySet
) -> None:
    outline = _outline(boundary_set)
    for edge in computation.edges:
        distance = _shape(edge).distance(outline)
        assert distance >= OUTER_CLEARANCE_DEGREES - CLEARANCE_SLACK, edge.source_codes


# --------------------------------------------------------------------------- #
# A known grid                                                                #
# --------------------------------------------------------------------------- #


def test_compute_on_grid_returns_exactly_the_shared_edges() -> None:
    boundary_set = grid_boundary_set(columns=3, rows=2)

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert [edge.source_codes for edge in result.edges] == [
        ("XX101", "XX102"),
        ("XX101", "XX104"),
        ("XX102", "XX103"),
        ("XX102", "XX105"),
        ("XX103", "XX106"),
        ("XX104", "XX105"),
        ("XX105", "XX106"),
    ]
    assert result.is_coverage_valid is True
    assert result.dropped_parts == 0


def test_compute_on_grid_never_returns_a_line_on_the_outline() -> None:
    boundary_set = grid_boundary_set(columns=3, rows=2)

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    _assert_off_outline(result, boundary_set)


def test_compute_on_grid_ends_edges_short_of_the_outline() -> None:
    boundary_set = grid_boundary_set(columns=3, rows=2)

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    first = _shape(result.edges[0])
    assert isinstance(first, LineString)
    latitudes = sorted(position[1] for position in first.coords)
    assert latitudes == pytest.approx([20.0 + OUTER_CLEARANCE_DEGREES, 21.0])
    assert {position[0] for position in first.coords} == {11.0}


def test_compute_with_districts_that_do_not_meet_returns_no_edge() -> None:
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(3, 0), (4, 0), (4, 1), (3, 1)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.edges == ()


def test_compute_with_districts_meeting_at_a_corner_returns_no_edge() -> None:
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.edges == ()


def test_compute_with_overlapping_districts_flags_invalid_coverage() -> None:
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(0.5, 0), (2, 0), (2, 1), (0.5, 1)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.is_coverage_valid is False
    assert result.invalid_coverage_districts == ("XX101", "XX102")
    _assert_off_outline(result, boundary_set)


def test_compute_with_gap_narrower_than_clearance_flags_invalid_coverage() -> None:
    # A 2e-4 degree (~20 m) sliver between two districts: wider than the snap
    # tolerance, so their edge is lost, but narrower than the outline clearance.
    gap = 2e-4
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(1 + gap, 0), (2, 0), (2, 1), (1 + gap, 1)]),
            "XX103": Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.is_coverage_valid is False
    assert result.invalid_coverage_districts == ("XX101", "XX102")
    _assert_off_outline(result, boundary_set)


def test_compute_with_gap_wider_than_clearance_keeps_coverage_valid() -> None:
    # A real gap (an unmapped area) is not a digitising error.
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(1.01, 0), (2, 0), (2, 1), (1.01, 1)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.is_coverage_valid is True
    assert result.invalid_coverage_districts == ()


def test_compute_with_custom_gap_width_uses_it() -> None:
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(1.01, 0), (2, 0), (2, 1), (1.01, 1)]),
        }
    )

    result = ShapelySharedEdgeCalculator(coverage_gap_width=0.02).compute(boundary_set)

    assert result.invalid_coverage_districts == ("XX101", "XX102")


def test_init_with_negative_gap_width_raises() -> None:
    with pytest.raises(ValueError, match="coverage_gap_width"):
        ShapelySharedEdgeCalculator(coverage_gap_width=-1.0)


def test_compute_with_short_shared_stretch_drops_it() -> None:
    # The two squares share only 0.0015 degrees of edge; after the clearance is
    # cut from both ends, less than the shortest drawable part is left.
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            "XX102": Polygon([(1, 0.9985), (2, 0.9985), (2, 2), (1, 2)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.edges == ()
    assert result.dropped_parts == 1


def test_compute_with_two_separate_stretches_returns_a_multilinestring() -> None:
    # A U-shaped district around a square, closed by a third district on top: the
    # U and the top district share two separate stretches.
    boundary_set = _boundary_set(
        {
            "XX101": Polygon(
                [(0, 0), (3, 0), (3, 2), (2, 2), (2, 1), (1, 1), (1, 2), (0, 2)]
            ),
            "XX102": Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
            "XX103": Polygon([(0, 2), (3, 2), (3, 3), (0, 3)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    edges = {edge.source_codes: edge for edge in result.edges}
    assert edges[("XX101", "XX103")].geometry.geojson.type == "MultiLineString"
    _assert_off_outline(result, boundary_set)


def test_compute_simplifies_wiggles_below_the_tolerance() -> None:
    wiggle = SIMPLIFY_TOLERANCE_DEGREES / 4
    steps = 40
    shared = [
        (1 + (wiggle if index % 2 else 0.0), index / steps)
        for index in range(steps + 1)
    ]
    boundary_set = _boundary_set(
        {
            "XX101": Polygon([(0, 0), *shared, (0, 1)]),
            "XX102": Polygon([*shared, (2, 1), (2, 0)]),
        }
    )

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    assert result.edges[0].geometry.position_count == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"snap_tolerance": 0.0},
        {"decimals": 0},
        {"decimals": 16},
        {"outer_clearance": SNAP_TOLERANCE_DEGREES},
    ],
)
def test_calculator_with_invalid_distances_is_rejected(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="must"):
        ShapelySharedEdgeCalculator(**overrides)


def test_calculator_square_helper_matches_grid_cells() -> None:
    # Guards the synthetic factory the grid tests rely on.
    cell = shape(square(10, 20, 11, 21).geojson.model_dump(mode="json"))

    assert cell.area == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Property: random coverages                                                  #
# --------------------------------------------------------------------------- #


@st.composite
def voronoi_coverages(draw: st.DrawFn) -> dict[str, BaseGeometry]:
    """Draw 3 to 10 Voronoi cells clipped to ``OUTER_RING``."""
    points = draw(
        st.lists(
            st.tuples(
                st.floats(min_value=0.02, max_value=0.98),
                st.floats(min_value=0.02, max_value=0.98),
            ),
            min_size=3,
            max_size=10,
            unique=True,
        )
    )
    cells = shapely.voronoi_polygons(MultiPoint(points), extend_to=OUTER_RING)
    clipped = [
        cell.intersection(OUTER_RING) for cell in shapely.get_parts(cells).tolist()
    ]
    return {
        f"XX1{index:02d}": cell
        for index, cell in enumerate(
            (cell for cell in clipped if cell.area > 1e-4), start=1
        )
        if cell.geom_type in {"Polygon", "MultiPolygon"}
    }


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
@given(voronoi_coverages())
def test_compute_on_random_coverages_keeps_off_outline_and_on_both_districts(
    polygons: dict[str, BaseGeometry],
) -> None:
    boundary_set = _boundary_set(polygons)

    result = ShapelySharedEdgeCalculator().compute(boundary_set)

    _assert_off_outline(result, boundary_set)
    pairs = [edge.source_codes for edge in result.edges]
    assert pairs == sorted(set(pairs))
    for edge in result.edges:
        first, second = (polygons[code].boundary for code in edge.source_codes)
        for position in itertools.chain.from_iterable(
            part.coords for part in shapely.get_parts(_shape(edge)).tolist()
        ):
            point = shapely.Point(position)
            assert point.distance(first) <= VERTEX_SLACK
            assert point.distance(second) <= VERTEX_SLACK
