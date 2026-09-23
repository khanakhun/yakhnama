"""Unit tests for ``yakhnama.shared_kernel.value_objects``."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from geojson_pydantic import Point
from geojson_pydantic.types import Position3D
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.value_objects import (
    EARLIEST_SUPPORTED_INSTANT,
    KNOWN_UNITS,
    LOCALIZED_TEXT_MAX_LENGTH,
    BoundingBox,
    Confidence,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
    LanguageCode,
    LocalizedText,
    Measurement,
    SiUnit,
    is_known_unit,
    is_language_code,
)

longitudes = st.floats(min_value=-180, max_value=180, allow_nan=False)
latitudes = st.floats(min_value=-90, max_value=90, allow_nan=False)
coordinates = st.builds(Coordinates, longitude=longitudes, latitude=latitudes)
utc_instants = st.datetimes(
    min_value=EARLIEST_SUPPORTED_INSTANT.replace(tzinfo=None),
    timezones=st.just(UTC),
)
precisions = st.sampled_from(DatePrecision)
language_codes = st.from_regex(r"\A[a-z]{2,3}(-[A-Z][a-z]{3})?\Z")
language_adapter: TypeAdapter[str] = TypeAdapter(LanguageCode)

# --------------------------------------------------------------------------- #
# Coordinates                                                                 #
# --------------------------------------------------------------------------- #


@given(point=coordinates)
def test_coordinates_geojson_round_trip_returns_equal_value(point: Coordinates) -> None:
    geojson = point.to_geojson_point()

    restored = Coordinates.from_geojson_point(geojson)

    assert restored == point
    assert geojson.coordinates.longitude == point.longitude


@given(point=coordinates)
def test_coordinates_json_round_trip_returns_equal_value(point: Coordinates) -> None:
    serialised = point.model_dump_json()

    restored = Coordinates.model_validate_json(serialised)

    assert restored == point


@given(
    longitude=st.one_of(
        st.floats(max_value=-180, exclude_max=True),
        st.floats(min_value=180, exclude_min=True),
        st.just(float("nan")),
    )
)
def test_coordinates_longitude_out_of_range_raises_validation_error(
    longitude: float,
) -> None:
    with pytest.raises(PydanticValidationError):
        Coordinates(longitude=longitude, latitude=0)


@given(
    latitude=st.one_of(
        st.floats(max_value=-90, exclude_max=True),
        st.floats(min_value=90, exclude_min=True),
    )
)
def test_coordinates_latitude_out_of_range_raises_validation_error(
    latitude: float,
) -> None:
    with pytest.raises(PydanticValidationError):
        Coordinates(longitude=0, latitude=latitude)


def test_coordinates_three_dimensional_point_raises_validation_error() -> None:
    point = Point(type="Point", coordinates=Position3D(74.6, 36.3, 2500.0))

    with pytest.raises(ValidationError, match="three-dimensional"):
        Coordinates.from_geojson_point(point)


def test_coordinates_assignment_raises_validation_error() -> None:
    point = Coordinates(longitude=74.6, latitude=36.3)

    with pytest.raises(PydanticValidationError):
        point.latitude = 0.0  # type: ignore[misc]  # reason: proves the frozen model rejects assignment


# --------------------------------------------------------------------------- #
# BoundingBox                                                                 #
# --------------------------------------------------------------------------- #


@given(points=st.lists(coordinates, min_size=1, max_size=20))
def test_bounding_box_from_coordinates_contains_every_point(
    points: list[Coordinates],
) -> None:
    box = BoundingBox.from_coordinates(points)

    contained = [box.contains(point) for point in points]

    assert all(contained)
    assert box.min_longitude == min(point.longitude for point in points)
    assert box.max_latitude == max(point.latitude for point in points)


def test_bounding_box_from_no_coordinates_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="at least one point"):
        BoundingBox.from_coordinates([])


def test_bounding_box_contains_point_outside_returns_false() -> None:
    box = BoundingBox(
        min_longitude=72, min_latitude=34, max_longitude=78, max_latitude=37
    )

    results = [
        box.contains(Coordinates(longitude=71.9, latitude=35)),
        box.contains(Coordinates(longitude=75, latitude=37.1)),
        box.contains(Coordinates(longitude=78, latitude=37)),
    ]

    assert results == [False, False, True]


def test_bounding_box_crossing_antimeridian_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="antimeridian"):
        BoundingBox(
            min_longitude=170, min_latitude=0, max_longitude=-170, max_latitude=10
        )


def test_bounding_box_inverted_latitudes_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="min_latitude"):
        BoundingBox(min_longitude=0, min_latitude=10, max_longitude=1, max_latitude=5)


# --------------------------------------------------------------------------- #
# Measurement                                                                 #
# --------------------------------------------------------------------------- #


@given(
    value=st.floats(allow_nan=False, allow_infinity=False),
    unit=st.sampled_from(sorted(KNOWN_UNITS)),
)
def test_measurement_json_round_trip_returns_equal_value(
    value: float, unit: str
) -> None:
    measurement = Measurement(value=value, unit=unit)

    restored = Measurement.model_validate_json(measurement.model_dump_json())

    assert restored == measurement


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_measurement_non_finite_value_raises_validation_error(value: float) -> None:
    with pytest.raises(PydanticValidationError):
        Measurement(value=value, unit=SiUnit.METRE)


@pytest.mark.parametrize("unit", ["furlong", "Metre", "", "metre/second", "m" * 65])
def test_measurement_unknown_or_malformed_unit_raises_validation_error(
    unit: str,
) -> None:
    with pytest.raises(PydanticValidationError):
        Measurement(value=1.0, unit=unit)


def test_known_units_matches_si_unit_members() -> None:
    members = {unit.value for unit in SiUnit}

    result = set(KNOWN_UNITS)

    assert result == members
    assert {"count", "cubic_metre_per_second", "metre_per_second"} <= result


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("cubic_metre", True),
        ("cubic_metre_per_second", True),
        ("metre_per_second", True),
        ("pakistani_rupee", False),
    ],
)
def test_is_known_unit_classifies_units(unit: str, *, expected: bool) -> None:
    result = is_known_unit(unit)

    assert result is expected


# --------------------------------------------------------------------------- #
# DateWithPrecision                                                           #
# --------------------------------------------------------------------------- #


@given(instant=utc_instants, precision=precisions)
def test_date_with_precision_truncate_twice_equals_truncate_once(
    instant: datetime, precision: DatePrecision
) -> None:
    date = DateWithPrecision(value=instant, precision=precision)

    once = date.truncate()

    assert once.truncate() == once
    assert once.value <= date.value
    assert once.precision is precision


@given(instant=utc_instants)
def test_date_with_precision_exact_truncate_is_unchanged(instant: datetime) -> None:
    date = DateWithPrecision(value=instant, precision=DatePrecision.EXACT)

    truncated = date.truncate()

    assert truncated == date


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        (DatePrecision.HOUR, datetime(2022, 8, 26, 14, tzinfo=UTC)),
        (DatePrecision.DAY, datetime(2022, 8, 26, tzinfo=UTC)),
        (DatePrecision.MONTH, datetime(2022, 8, 1, tzinfo=UTC)),
        (DatePrecision.SEASON, datetime(2022, 6, 1, tzinfo=UTC)),
        (DatePrecision.YEAR, datetime(2022, 1, 1, tzinfo=UTC)),
    ],
)
def test_date_with_precision_truncate_floors_to_period_start(
    precision: DatePrecision, expected: datetime
) -> None:
    date = DateWithPrecision(
        value=datetime(2022, 8, 26, 14, 35, 12, 500, tzinfo=UTC), precision=precision
    )

    truncated = date.truncate()

    assert truncated.value == expected


@pytest.mark.parametrize(
    ("month", "expected_year", "expected_month"),
    [
        (1, 2021, 12),
        (2, 2021, 12),
        (3, 2022, 3),
        (5, 2022, 3),
        (6, 2022, 6),
        (8, 2022, 6),
        (9, 2022, 9),
        (11, 2022, 9),
        (12, 2022, 12),
    ],
)
def test_date_with_precision_season_floors_to_meteorological_quarter(
    month: int, expected_year: int, expected_month: int
) -> None:
    date = DateWithPrecision(
        value=datetime(2022, month, 15, 9, tzinfo=UTC), precision=DatePrecision.SEASON
    )

    truncated = date.truncate()

    assert truncated.value == datetime(expected_year, expected_month, 1, tzinfo=UTC)


def test_date_with_precision_offset_datetime_is_normalised_to_utc() -> None:
    karachi = timezone(timedelta(hours=5))

    date = DateWithPrecision(
        value=datetime(2022, 8, 27, 3, tzinfo=karachi), precision=DatePrecision.DAY
    )

    assert date.value == datetime(2022, 8, 26, 22, tzinfo=UTC)
    assert date.value.tzinfo is UTC


def test_date_with_precision_naive_datetime_raises_validation_error() -> None:
    naive = datetime(2022, 8, 26, tzinfo=UTC).replace(tzinfo=None)

    with pytest.raises(PydanticValidationError):
        DateWithPrecision(value=naive, precision=DatePrecision.DAY)


def test_date_with_precision_before_supported_range_raises_validation_error() -> None:
    too_early = EARLIEST_SUPPORTED_INSTANT - timedelta(microseconds=1)

    with pytest.raises(PydanticValidationError, match="earlier"):
        DateWithPrecision(value=too_early, precision=DatePrecision.YEAR)


def test_date_with_precision_utc_overflow_raises_validation_error() -> None:
    west = timezone(timedelta(hours=-5))
    latest_local = datetime.max.replace(tzinfo=west)

    with pytest.raises(PydanticValidationError, match="representable"):
        DateWithPrecision(value=latest_local, precision=DatePrecision.EXACT)


@given(instant=utc_instants, precision=precisions)
def test_date_with_precision_json_round_trip_returns_equal_value(
    instant: datetime, precision: DatePrecision
) -> None:
    date = DateWithPrecision(value=instant, precision=precision)

    restored = DateWithPrecision.model_validate_json(date.model_dump_json())

    assert restored == date


# --------------------------------------------------------------------------- #
# LanguageCode and LocalizedText                                              #
# --------------------------------------------------------------------------- #


@given(code=language_codes)
def test_language_code_canonical_form_is_unchanged(code: str) -> None:
    validated = language_adapter.validate_python(code)

    assert validated == code
    assert is_language_code(code)


@given(code=language_codes)
def test_language_code_any_casing_normalises_to_canonical(code: str) -> None:
    shouted = code.upper()

    validated = language_adapter.validate_python(shouted)

    assert validated == code


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("EN", "en"), ("ur-arab", "ur-Arab"), ("SHI-LATN", "shi-Latn"), ("bsk", "bsk")],
)
def test_language_code_examples_normalise(raw: str, expected: str) -> None:
    validated = language_adapter.validate_python(raw)

    assert validated == expected


@pytest.mark.parametrize(
    "raw", ["e", "engl", "en-US", "en_Latn", "ur-Arab-PK", "1a", "", "en-", "en-Ar"]
)
def test_language_code_outside_subset_is_rejected(raw: str) -> None:
    with pytest.raises(PydanticValidationError):
        language_adapter.validate_python(raw)

    assert is_language_code(raw) is False


def test_language_code_non_string_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        language_adapter.validate_python(42)


texts_strategy = st.dictionaries(
    language_codes,
    st.text(min_size=1, max_size=50),
    min_size=1,
    max_size=5,
)


@given(texts=texts_strategy)
def test_localized_text_json_round_trip_returns_equal_value(
    texts: dict[str, str],
) -> None:
    localized = LocalizedText(texts=texts)

    restored = LocalizedText.model_validate_json(localized.model_dump_json())

    assert restored == localized
    assert hash(restored) == hash(localized)
    assert localized.model_dump() == {"texts": texts}


def test_localized_text_keys_are_normalised() -> None:
    localized = LocalizedText(texts={"UR-arab": "ہنزہ", "EN": "Hunza"})

    keys = set(localized.texts)

    assert keys == {"ur-Arab", "en"}


def test_localized_text_empty_mapping_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        LocalizedText(texts={})


def test_localized_text_too_long_text_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        LocalizedText(texts={"en": "x" * (LOCALIZED_TEXT_MAX_LENGTH + 1)})


def test_localized_text_mapping_mutation_raises_type_error() -> None:
    localized = LocalizedText(texts={"en": "Hunza"})

    with pytest.raises(TypeError):
        localized.texts["en"] = "changed"  # type: ignore[index]  # reason: proves the mapping is read-only at runtime


def test_localized_text_get_preferred_language_present_returns_it() -> None:
    localized = LocalizedText(texts={"en": "Hunza", "ur-Arab": "ہنزہ"})

    result = localized.get("UR-arab", fallback_order=["en"])

    assert result == "ہنزہ"


def test_localized_text_get_falls_back_in_order() -> None:
    localized = LocalizedText(texts={"en": "Hunza", "ur-Arab": "ہنزہ"})

    result = localized.get("bsk-Arab", fallback_order=["shi", "ur-arab", "en"])

    assert result == "ہنزہ"


def test_localized_text_get_no_requested_language_returns_none() -> None:
    localized = LocalizedText(texts={"en": "Hunza"})

    result = localized.get("ur")

    assert result is None


# --------------------------------------------------------------------------- #
# Confidence                                                                  #
# --------------------------------------------------------------------------- #


def test_confidence_members_are_low_medium_high() -> None:
    values = [member.value for member in Confidence]

    assert values == ["low", "medium", "high"]


def test_date_precision_members_are_the_glossary_values() -> None:
    values = [member.value for member in DatePrecision]

    assert values == ["exact", "hour", "day", "month", "season", "year"]


@pytest.mark.parametrize(
    ("unit", "member"),
    [
        ("cubic_metre_per_second", SiUnit.CUBIC_METRE_PER_SECOND),
        ("metre_per_second", SiUnit.METRE_PER_SECOND),
    ],
)
def test_measurement_compound_unit_is_accepted(unit: str, member: SiUnit) -> None:
    measurement = Measurement(value=1250.5, unit=unit)

    restored = Measurement.model_validate_json(measurement.model_dump_json())

    assert measurement.unit == member
    assert restored == measurement
