"""District boundary value objects, matching and the edge snapshot entity.

Every district is synthetic (``tests/factories/boundaries.py``).
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from geojson_pydantic import LineString, MultiLineString, Point
from geojson_pydantic.types import Position2D, Position3D
from pydantic import ValidationError

from tests.factories.boundaries import (
    SYNTHETIC_SHA256,
    attribution,
    boundary_source,
    grid_boundary_set,
    line,
    square,
)
from yakhnama.modules.geography.domain.boundaries import (
    BoundaryAttribution,
    DistrictBoundarySet,
    DistrictCentroid,
    DistrictEdge,
    DistrictEdgeSet,
    DistrictLink,
    EdgeGeometry,
    GazetteerDistrict,
    SharedEdge,
    SourceDistrict,
    match_district_boundaries,
)
from yakhnama.modules.geography.domain.value_objects import PlaceGeometry
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Coordinates

EDGE_SET_ID = EntityId("0192a3b4-0000-7000-8000-000000000001")
INSIDE = Coordinates(longitude=10.5, latitude=20.5)
CENTROID = DistrictCentroid(source_code="XX101", place_code="pk.gb.xx101", point=INSIDE)
CREATED_AT = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


def _gazetteer(
    *codes: str, inactive: tuple[str, ...] = ()
) -> dict[str, GazetteerDistrict]:
    return {
        code: GazetteerDistrict(
            code=code,
            name=f"Synthetic {code.rsplit('.', 1)[-1].upper()}",
            is_active=code not in inactive,
        )
        for code in codes
    }


def _edge(first: str = "XX101", second: str = "XX102") -> DistrictEdge:
    return DistrictEdge(
        source_codes=(first, second),
        place_codes=("pk.gb.xx101", None),
        geometry=line((11.0, 20.0), (11.0, 21.0)),
    )


def _edge_set(
    *edges: DistrictEdge, created_at: datetime = CREATED_AT
) -> DistrictEdgeSet:
    return DistrictEdgeSet(
        id=EDGE_SET_ID,
        region_code="XX1",
        attribution=attribution(),
        sha256=SYNTHETIC_SHA256,
        fingerprint=DistrictEdgeSet.fingerprint_of("XX1", SYNTHETIC_SHA256, edges),
        edges=edges,
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# Attribution and source                                                      #
# --------------------------------------------------------------------------- #


def test_attribution_with_offset_time_is_normalised_to_utc() -> None:
    plus_five = timezone(timedelta(hours=5))

    result = attribution(datetime(2026, 10, 5, 17, 0, tzinfo=plus_five))

    assert result.retrieved_at == datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    assert result.retrieved_at.tzinfo is UTC


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/data",
        "https://user:secret@example.org/",
        "https:///no-host",
        "https://example.org/with space",
        "ftp://example.org/file",
    ],
)
def test_attribution_with_unsafe_url_is_rejected(url: str) -> None:
    fields = attribution().model_dump()

    with pytest.raises(ValidationError):
        BoundaryAttribution.model_validate({**fields, "source_url": url})


def test_boundary_source_with_duplicate_source_code_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicated source codes"):
        boundary_source([("XX101", "pk.gb.a"), ("XX101", "pk.gb.b")])


def test_boundary_source_with_duplicate_place_code_is_rejected() -> None:
    with pytest.raises(ValidationError, match="place codes"):
        boundary_source([("XX101", "pk.gb.a"), ("XX102", "pk.gb.a")])


def test_boundary_source_with_several_unlinked_rows_is_valid() -> None:
    source = boundary_source([("XX101", None), ("XX102", None)])

    assert [link.place_code for link in source.links] == [None, None]


def test_boundary_source_link_for_returns_row_or_none() -> None:
    source = boundary_source([("XX101", "pk.gb.a")])

    found = source.link_for("XX101")
    missing = source.link_for("XX199")

    assert found is not None
    assert found.place_code == "pk.gb.a"
    assert missing is None


def test_boundary_source_attribution_carries_retrieval_time() -> None:
    source = boundary_source([("XX101", None)])
    retrieved_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    result = source.attribution(retrieved_at)

    assert result.retrieved_at == retrieved_at
    assert result.licence == source.licence
    assert result.source_url == source.source_url


def test_district_link_with_invalid_place_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DistrictLink(source_code="XX101", source_name="Synthetic", place_code="PK GB")


# --------------------------------------------------------------------------- #
# Districts read from a file                                                  #
# --------------------------------------------------------------------------- #


def test_source_district_with_point_geometry_is_rejected() -> None:
    point = PlaceGeometry(
        geojson=Point(
            type="Point", coordinates=Position2D(longitude=10.0, latitude=20.0)
        )
    )

    with pytest.raises(ValidationError, match="Polygon or MultiPolygon"):
        SourceDistrict(
            code="XX101", name="Synthetic", geometry=point, representative_point=INSIDE
        )


def test_boundary_set_with_duplicate_district_codes_is_rejected() -> None:
    district = SourceDistrict(
        code="XX101",
        name="Synthetic",
        geometry=square(10, 20, 11, 21),
        representative_point=INSIDE,
    )

    with pytest.raises(ValidationError, match="duplicated district codes"):
        DistrictBoundarySet(
            attribution=attribution(),
            sha256=SYNTHETIC_SHA256,
            region_code="XX1",
            districts=(district, district),
        )


# --------------------------------------------------------------------------- #
# Matching                                                                    #
# --------------------------------------------------------------------------- #


def test_match_with_every_district_linked_reports_no_mismatch() -> None:
    boundary_set = grid_boundary_set(columns=2, rows=1)
    source = boundary_source([("XX101", "pk.gb.xx101"), ("XX102", "pk.gb.xx102")])

    match = match_district_boundaries(
        source,
        boundary_set,
        places=_gazetteer("pk.gb.xx101", "pk.gb.xx102"),
        region_districts=["pk.gb.xx101", "pk.gb.xx102"],
    )

    assert [link.place_code for link in match.linked] == ["pk.gb.xx101", "pk.gb.xx102"]
    assert match.has_mismatches is False
    assert match.place_code_of("XX102") == "pk.gb.xx102"


def test_match_reports_every_kind_of_mismatch_without_guessing() -> None:
    boundary_set = grid_boundary_set(columns=3, rows=2)
    source = boundary_source(
        [
            ("XX101", "pk.gb.xx101"),
            ("XX102", None),
            ("XX103", "pk.gb.missing"),
            ("XX104", "pk.gb.retired"),
            ("XX105", "pk.gb.renamed"),
            ("XX199", "pk.gb.elsewhere"),
        ]
    )
    places = _gazetteer("pk.gb.xx101", "pk.gb.retired", inactive=("pk.gb.retired",))
    places["pk.gb.renamed"] = GazetteerDistrict(
        code="pk.gb.renamed", name="Another Spelling", is_active=True
    )

    match = match_district_boundaries(
        source,
        boundary_set,
        places=places,
        region_districts=["pk.gb.xx101", "pk.gb.renamed", "pk.gb.orphan"],
    )

    assert [link.source_code for link in match.linked] == ["XX101", "XX105"]
    assert [link.source_code for link in match.unlinked] == ["XX102"]
    assert match.not_in_link_table == ("XX106",)
    assert match.not_in_source == ("XX199",)
    assert match.missing_places == ("pk.gb.missing", "pk.gb.retired")
    assert match.places_without_boundary == ("pk.gb.orphan",)
    assert [
        (item.source_code, item.compared_with) for item in match.name_differences
    ] == [("XX105", "gazetteer")]
    assert match.place_code_of("XX102") is None
    assert match.has_mismatches is True


def test_match_with_renamed_district_in_file_reports_link_table_difference() -> None:
    boundary_set = grid_boundary_set(columns=1, rows=1)
    source = boundary_source([("XX101", None)])
    renamed = source.model_copy(
        update={
            "links": (
                DistrictLink(source_code="XX101", source_name="Old", place_code=None),
            )
        }
    )

    match = match_district_boundaries(
        renamed, boundary_set, places={}, region_districts=[]
    )

    assert match.name_differences[0].compared_with == "link_table"
    assert match.name_differences[0].other_name == "Old"


# --------------------------------------------------------------------------- #
# Edges                                                                       #
# --------------------------------------------------------------------------- #


def test_edge_geometry_counts_positions_over_every_part() -> None:
    geometry = EdgeGeometry(
        geojson=MultiLineString(
            type="MultiLineString",
            coordinates=[
                [
                    Position2D(longitude=1.0, latitude=1.0),
                    Position2D(longitude=2.0, latitude=2.0),
                ],
                [
                    Position2D(longitude=3.0, latitude=3.0),
                    Position2D(longitude=4.0, latitude=4.0),
                    Position2D(longitude=5.0, latitude=5.0),
                ],
            ],
        )
    )

    assert geometry.position_count == 5


def test_edge_geometry_with_altitude_is_rejected() -> None:
    with pytest.raises(ValidationError, match="two-dimensional"):
        EdgeGeometry.model_validate(
            {
                "geojson": {
                    "type": "LineString",
                    "coordinates": [
                        Position3D(longitude=1.0, latitude=1.0, altitude=5.0),
                        Position3D(longitude=2.0, latitude=2.0, altitude=5.0),
                    ],
                }
            }
        )


@pytest.mark.parametrize(
    "position", [(181.0, 1.0), (1.0, -91.0), (float("nan"), 1.0), (1.0, float("inf"))]
)
def test_edge_geometry_outside_wgs84_is_rejected(position: tuple[float, float]) -> None:
    with pytest.raises(ValidationError, match="WGS84"):
        line(position, (1.0, 1.0))


def test_edge_geometry_copies_the_model_it_is_given() -> None:
    original = line((1.0, 1.0), (2.0, 2.0)).geojson
    assert isinstance(original, LineString)

    wrapped = EdgeGeometry(geojson=original)
    original.coordinates[0] = Position2D(longitude=9.0, latitude=9.0)

    assert wrapped.geojson.coordinates[0] == Position2D(longitude=1.0, latitude=1.0)


@pytest.mark.parametrize("pair", [("XX102", "XX101"), ("XX101", "XX101")])
def test_shared_edge_with_unsorted_or_repeated_pair_is_rejected(
    pair: tuple[str, str],
) -> None:
    with pytest.raises(ValidationError, match="sorted order"):
        SharedEdge(source_codes=pair, geometry=line((1.0, 1.0), (2.0, 2.0)))


def test_district_edge_from_shared_edge_takes_place_codes_from_match() -> None:
    boundary_set = grid_boundary_set(columns=2, rows=1)
    match = match_district_boundaries(
        boundary_source([("XX101", "pk.gb.xx101"), ("XX102", None)]),
        boundary_set,
        places=_gazetteer("pk.gb.xx101"),
        region_districts=[],
    )
    shared = SharedEdge(
        source_codes=("XX101", "XX102"), geometry=line((11.0, 20.0), (11.0, 21.0))
    )

    edge = DistrictEdge.from_shared_edge(shared, match)

    assert edge.place_codes == ("pk.gb.xx101", None)
    assert edge.is_fully_linked is False


def test_district_edge_with_both_places_is_fully_linked() -> None:
    edge = _edge().model_copy(update={"place_codes": ("pk.gb.a", "pk.gb.b")})

    assert edge.is_fully_linked is True


# --------------------------------------------------------------------------- #
# Edge set                                                                    #
# --------------------------------------------------------------------------- #


def test_edge_set_with_matching_fingerprint_is_valid_and_counts_positions() -> None:
    edge_set = _edge_set(_edge(), _edge("XX101", "XX104"))

    assert edge_set.position_count == 4
    assert edge_set.created_at.tzinfo is UTC


def test_edge_set_with_wrong_fingerprint_is_rejected() -> None:
    valid = _edge_set(_edge())

    with pytest.raises(ValidationError, match="fingerprint"):
        DistrictEdgeSet.model_validate({**valid.model_dump(), "fingerprint": "b" * 64})


def test_edge_set_with_duplicate_pair_is_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicated pairs"):
        _edge_set(_edge(), _edge())


def test_edge_set_fingerprint_ignores_retrieval_time_but_not_content() -> None:
    edges = (_edge(),)

    first = DistrictEdgeSet.fingerprint_of("XX1", SYNTHETIC_SHA256, edges)
    other_file = DistrictEdgeSet.fingerprint_of("XX1", "c" * 64, edges)
    other_edges = DistrictEdgeSet.fingerprint_of("XX1", SYNTHETIC_SHA256, ())

    assert len({first, other_file, other_edges}) == 3
    assert first == DistrictEdgeSet.fingerprint_of("XX1", SYNTHETIC_SHA256, edges)


def test_edge_set_records_centroids_and_finds_them_by_place() -> None:
    centroids = (CENTROID,)
    edge_set = DistrictEdgeSet(
        id=EDGE_SET_ID,
        region_code="XX1",
        attribution=attribution(),
        sha256=SYNTHETIC_SHA256,
        fingerprint=DistrictEdgeSet.fingerprint_of(
            "XX1", SYNTHETIC_SHA256, (), centroids
        ),
        edges=(),
        centroids=centroids,
        created_at=CREATED_AT,
    )

    assert edge_set.centroid_set_for("pk.gb.xx101") == INSIDE
    assert edge_set.centroid_set_for("pk.gb.other") is None


def test_edge_set_fingerprint_changes_with_centroids() -> None:
    without = DistrictEdgeSet.fingerprint_of("XX1", SYNTHETIC_SHA256, ())
    with_centroid = DistrictEdgeSet.fingerprint_of(
        "XX1", SYNTHETIC_SHA256, (), (CENTROID,)
    )

    assert without != with_centroid


def test_edge_set_with_two_centroids_for_one_place_is_rejected() -> None:
    centroids = (CENTROID, CENTROID.model_copy(update={"source_code": "XX102"}))

    with pytest.raises(ValidationError, match="duplicated centroids"):
        DistrictEdgeSet(
            id=EDGE_SET_ID,
            region_code="XX1",
            attribution=attribution(),
            sha256=SYNTHETIC_SHA256,
            fingerprint=DistrictEdgeSet.fingerprint_of(
                "XX1", SYNTHETIC_SHA256, (), centroids
            ),
            edges=(),
            centroids=centroids,
            created_at=CREATED_AT,
        )
