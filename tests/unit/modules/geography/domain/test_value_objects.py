"""Unit tests for ``yakhnama.modules.geography.domain.value_objects``."""

import unicodedata
import uuid

import pytest
from geojson_pydantic import MultiPolygon, Point, Polygon
from geojson_pydantic.types import Position, Position2D, Position3D
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.geography.domain import value_objects
from yakhnama.modules.geography.domain.value_objects import (
    PLACE_CODE_PATTERN,
    PLACE_NAME_MAX_LENGTH,
    AdminLevel,
    PlaceCode,
    PlaceGeometry,
    PlaceName,
    ScriptCode,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates

ids = Uuid7Generator()
PLACE_CODE: TypeAdapter[str] = TypeAdapter(PlaceCode)
LEVELS = st.sampled_from(list(AdminLevel))
LONGITUDES = st.floats(min_value=-180.0, max_value=180.0)
LATITUDES = st.floats(min_value=-90.0, max_value=90.0)


@st.composite
def bounding_boxes(draw: st.DrawFn) -> BoundingBox:
    """Draw a valid box by sorting two random corners."""
    west, east = sorted((draw(LONGITUDES), draw(LONGITUDES)))
    south, north = sorted((draw(LATITUDES), draw(LATITUDES)))
    return BoundingBox(
        min_longitude=west, min_latitude=south, max_longitude=east, max_latitude=north
    )


def _ring(west: float, south: float, east: float, north: float) -> list[Position]:
    return [
        Position2D(longitude=west, latitude=south),
        Position2D(longitude=east, latitude=south),
        Position2D(longitude=east, latitude=north),
        Position2D(longitude=west, latitude=north),
        Position2D(longitude=west, latitude=south),
    ]


# --------------------------------------------------------------------------- #
# AdminLevel                                                                  #
# --------------------------------------------------------------------------- #


def test_admin_level_rank_follows_declared_hierarchy_top_down() -> None:
    expected = [
        "country",
        "province_or_region",
        "division",
        "district",
        "tehsil",
        "union_council",
        "village",
    ]

    ordered = [level.value for level in sorted(AdminLevel, key=lambda lv: lv.rank)]

    assert ordered == expected


@given(level=LEVELS)
def test_admin_level_is_below_itself_returns_false(level: AdminLevel) -> None:
    result = level.is_below(level)

    assert result is False


@given(first=LEVELS, second=LEVELS)
def test_admin_level_is_below_distinct_levels_is_asymmetric_and_total(
    first: AdminLevel, second: AdminLevel
) -> None:
    forward = first.is_below(second)
    backward = second.is_below(first)

    assert not (forward and backward)
    assert (forward or backward) == (first is not second)


@given(first=LEVELS, second=LEVELS, third=LEVELS)
def test_admin_level_is_below_chain_is_transitive(
    first: AdminLevel, second: AdminLevel, third: AdminLevel
) -> None:
    is_chain = first.is_below(second) and second.is_below(third)

    result = first.is_below(third)

    assert result or not is_chain


@given(level=LEVELS, parent=LEVELS)
def test_admin_level_can_be_child_of_matches_strictly_lower_rule(
    level: AdminLevel, parent: AdminLevel
) -> None:
    result = level.can_be_child_of(parent)

    assert result == level.is_below(parent)


@given(level=LEVELS)
def test_admin_level_can_be_child_of_none_only_for_country(level: AdminLevel) -> None:
    result = level.can_be_child_of(None)

    assert result == (level is AdminLevel.COUNTRY)


# --------------------------------------------------------------------------- #
# PlaceCode and ScriptCode                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", ["pk", "pk.gb.hunza", "pk-gb_01", "0a", "a" * 64])
def test_place_code_valid_codes_are_accepted(code: str) -> None:
    result = PLACE_CODE.validate_python(code)

    assert result == code


@pytest.mark.parametrize(
    "code", ["p", "PK.gb", ".pk", "_pk", "pk gb", "pk/gb", "a" * 65, "hunzā", ""]
)
def test_place_code_invalid_codes_raise_validation_error(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        PLACE_CODE.validate_python(code)


@given(code=st.from_regex(PLACE_CODE_PATTERN, fullmatch=True))
def test_place_code_any_pattern_match_is_accepted(code: str) -> None:
    result = PLACE_CODE.validate_python(code)

    assert result == code


def test_script_code_values_are_iso_15924_title_case() -> None:
    values = sorted(script.value for script in ScriptCode)

    assert values == ["Arab", "Latn", "Tibt"]


# --------------------------------------------------------------------------- #
# PlaceName                                                                   #
# --------------------------------------------------------------------------- #


def test_place_name_defaults_are_official_not_preferred_without_script() -> None:
    name = PlaceName(text="Hunza", language="en")

    assert (name.kind, name.is_preferred, name.script, name.source_id) == (
        "official",
        False,
        None,
        None,
    )


def test_place_name_text_is_stripped_and_nfc_normalised() -> None:
    decomposed = "  Hunzā  "  # "a" followed by a combining macron

    name = PlaceName(text=decomposed, language="en")

    assert name.text == "Hunzā"


@given(text=st.text(min_size=1, max_size=50))
def test_place_name_normalisation_is_idempotent(text: str) -> None:
    candidate = unicodedata.normalize("NFC", text).strip()
    if not candidate:
        return
    name = PlaceName(text=text, language="en")

    again = PlaceName(text=name.text, language="en")

    assert again.text == name.text == candidate


def test_place_name_language_is_normalised() -> None:
    name = PlaceName(text="ہنزہ", language="UR-arab", script=ScriptCode.ARAB)

    assert name.language == "ur-Arab"


@pytest.mark.parametrize("text", ["", "   ", "a" * (PLACE_NAME_MAX_LENGTH + 1)])
def test_place_name_text_out_of_bounds_raises_validation_error(text: str) -> None:
    with pytest.raises(PydanticValidationError):
        PlaceName(text=text, language="en")


def test_place_name_non_string_text_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        PlaceName.model_validate({"text": 42, "language": "en"})


def test_place_name_text_at_max_length_is_accepted() -> None:
    text = "a" * PLACE_NAME_MAX_LENGTH

    name = PlaceName(text=text, language="en")

    assert len(name.text) == PLACE_NAME_MAX_LENGTH


def test_place_name_script_contradicting_language_subtag_raises_validation_error() -> (
    None
):
    with pytest.raises(PydanticValidationError, match="contradicts"):
        PlaceName(text="Hunza", language="ur-Arab", script=ScriptCode.LATN)


def test_place_name_script_agreeing_with_language_subtag_is_accepted() -> None:
    name = PlaceName(text="Hunza", language="shi-Latn", script=ScriptCode.LATN)

    assert name.script is ScriptCode.LATN


@pytest.mark.parametrize("kind", ["nickname", "OFFICIAL", ""])
def test_place_name_unknown_kind_raises_validation_error(kind: str) -> None:
    with pytest.raises(PydanticValidationError):
        PlaceName.model_validate({"text": "Hunza", "language": "en", "kind": kind})


def test_place_name_source_id_that_is_not_uuid7_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        PlaceName(text="Hunza", language="en", source_id=uuid.uuid4())


def test_place_name_source_id_uuid7_is_kept() -> None:
    source_id = ids.new_id()

    name = PlaceName(text="Hunza", language="en", source_id=source_id)

    assert name.source_id == source_id


def test_place_name_key_is_text_language_script() -> None:
    name = PlaceName(text="Hunza", language="en", script=ScriptCode.LATN)

    assert name.key == ("Hunza", "en", ScriptCode.LATN)


def test_place_name_with_preference_returns_new_instance() -> None:
    name = PlaceName(text="Hunza", language="en")

    preferred = name.with_preference(is_preferred=True)

    assert preferred.is_preferred is True
    assert name.is_preferred is False
    assert preferred.key == name.key


def test_place_name_assignment_raises_validation_error() -> None:
    name = PlaceName(text="Hunza", language="en")

    with pytest.raises(PydanticValidationError):
        name.text = "Nagar"  # type: ignore[misc]  # reason: proves the model is frozen


@given(
    text=st.text(
        alphabet=st.characters(categories=("L", "N")), min_size=1, max_size=40
    ),
    language=st.sampled_from(["en", "ur", "shi", "bsk", "bft", "wbl", "khw"]),
    script=st.none() | st.sampled_from(list(ScriptCode)),
    kind=st.sampled_from(["official", "alternative", "historical", "transliteration"]),
    is_preferred=st.booleans(),
)
def test_place_name_round_trip_returns_equal_name(
    text: str,
    language: str,
    script: ScriptCode | None,
    kind: str,
    *,
    is_preferred: bool,
) -> None:
    name = PlaceName.model_validate(
        {
            "text": text,
            "language": language,
            "script": script,
            "kind": kind,
            "is_preferred": is_preferred,
        }
    )

    restored = PlaceName.model_validate_json(name.model_dump_json())

    assert restored == name


# --------------------------------------------------------------------------- #
# PlaceGeometry                                                               #
# --------------------------------------------------------------------------- #


def test_place_geometry_srid_is_wgs84() -> None:
    srid = PlaceGeometry.SRID

    assert srid == 4326


def test_place_geometry_point_bounding_box_is_degenerate_box() -> None:
    geometry = PlaceGeometry.from_coordinates(
        Coordinates(longitude=74.65, latitude=36.32)
    )

    box = geometry.bounding_box()

    assert (geometry.geometry_type, box) == (
        "Point",
        BoundingBox(
            min_longitude=74.65,
            min_latitude=36.32,
            max_longitude=74.65,
            max_latitude=36.32,
        ),
    )


@given(box=bounding_boxes())
def test_place_geometry_from_bounding_box_round_trips_its_box(box: BoundingBox) -> None:
    geometry = PlaceGeometry.from_bounding_box(box)

    result = geometry.bounding_box()

    assert result == box
    assert geometry.geometry_type == "Polygon"


def test_place_geometry_multipolygon_bounding_box_covers_every_polygon() -> None:
    geojson = MultiPolygon(
        type="MultiPolygon",
        coordinates=[[_ring(74.0, 36.0, 74.5, 36.5)], [_ring(75.0, 35.0, 75.5, 35.5)]],
    )

    box = PlaceGeometry(geojson=geojson).bounding_box()

    assert box == BoundingBox(
        min_longitude=74.0, min_latitude=35.0, max_longitude=75.5, max_latitude=36.5
    )


def test_place_geometry_from_geojson_dict_selects_class_by_type() -> None:
    payload = {"geojson": {"type": "Point", "coordinates": [74.6, 36.3]}}

    geometry = PlaceGeometry.model_validate(payload)

    assert isinstance(geometry.geojson, Point)


def test_place_geometry_input_is_copied_so_caller_mutation_does_not_leak() -> None:
    polygon = Polygon(type="Polygon", coordinates=[_ring(74.0, 36.0, 74.5, 36.5)])
    geometry = PlaceGeometry(geojson=polygon)

    polygon.coordinates[0][1] = Position2D(longitude=80.0, latitude=36.0)

    assert geometry.bounding_box().max_longitude == 74.5


@pytest.mark.parametrize(
    "geojson",
    [
        Point(type="Point", coordinates=Position2D(longitude=180.5, latitude=0.0)),
        Point(type="Point", coordinates=Position2D(longitude=0.0, latitude=-90.5)),
        Point(type="Point", coordinates=Position2D(longitude=float("nan"), latitude=0)),
        Point(type="Point", coordinates=Position2D(longitude=0, latitude=float("inf"))),
        Point(
            type="Point",
            coordinates=Position3D(longitude=74.0, latitude=36.0, altitude=2400.0),
        ),
        Polygon(type="Polygon", coordinates=[]),
        MultiPolygon(type="MultiPolygon", coordinates=[]),
    ],
    ids=[
        "longitude-out-of-range",
        "latitude-out-of-range",
        "longitude-nan",
        "latitude-infinite",
        "three-dimensional",
        "empty-polygon",
        "empty-multipolygon",
    ],
)
def test_place_geometry_invalid_positions_raise_validation_error(
    geojson: Point | Polygon | MultiPolygon,
) -> None:
    with pytest.raises(PydanticValidationError):
        PlaceGeometry(geojson=geojson)


def test_place_geometry_more_positions_than_the_bound_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(value_objects, "_MAX_POSITIONS", 4)
    polygon = Polygon(type="Polygon", coordinates=[_ring(74.0, 36.0, 74.5, 36.5)])

    with pytest.raises(PydanticValidationError, match="more than 4 positions"):
        PlaceGeometry(geojson=polygon)


def test_place_geometry_unknown_geojson_type_raises_validation_error() -> None:
    payload = {"geojson": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}

    with pytest.raises(PydanticValidationError):
        PlaceGeometry.model_validate(payload)


@given(box=bounding_boxes())
def test_place_geometry_round_trip_returns_equal_geometry(box: BoundingBox) -> None:
    geometry = PlaceGeometry.from_bounding_box(box)

    restored = PlaceGeometry.model_validate_json(geometry.model_dump_json())

    assert restored == geometry
