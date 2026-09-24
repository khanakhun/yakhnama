"""Unit tests for ``yakhnama.shared_kernel.privacy``."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.privacy import (
    MAX_PUBLIC_DECIMALS,
    PublicCoordinatePolicy,
    round_coordinates,
)
from yakhnama.shared_kernel.value_objects import Coordinates

# Mean Earth radius (IUGG), for the haversine distance used in the bound test.
EARTH_RADIUS_KM = 6371.0088
# The documented bound at 2 decimals for latitudes 34-38 degrees north: at most
# 0.005 degrees per axis, about 0.56 km north-south and 0.46 km east-west.
TWO_DECIMALS_BOUND_KM = 0.73
NORTH_SOUTH_BOUND_KM = 0.56
# Floats carry about 1e-13 degrees of representation error at these magnitudes.
FLOAT_TOLERANCE_DEGREES = 1e-9

coordinates = st.builds(
    Coordinates,
    longitude=st.floats(min_value=-180, max_value=180, allow_nan=False),
    latitude=st.floats(min_value=-90, max_value=90, allow_nan=False),
)
# A test envelope around northern Pakistan with margin, not a boundary claim.
regional_coordinates = st.builds(
    Coordinates,
    longitude=st.floats(min_value=70, max_value=78),
    latitude=st.floats(min_value=34, max_value=38),
)
decimals = st.integers(min_value=0, max_value=MAX_PUBLIC_DECIMALS)


def _haversine_km(first: Coordinates, second: Coordinates) -> float:
    latitude_1, latitude_2 = math.radians(first.latitude), math.radians(second.latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude = math.radians(second.longitude - first.longitude)
    half_chord = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_1)
        * math.cos(latitude_2)
        * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(half_chord))


@pytest.mark.parametrize(
    ("longitude", "latitude", "places", "expected"),
    [
        (74.3149, 36.125, 2, (74.31, 36.13)),
        (-74.3151, -36.125, 2, (-74.32, -36.13)),
        (0.5, -0.5, 0, (1.0, -1.0)),
        (179.9999, 89.9999, 3, (180.0, 90.0)),
        (74.1234565, 35.9212345, 6, (74.123457, 35.921235)),
    ],
)
def test_round_coordinates_known_values_rounds_half_away_from_zero(
    longitude: float, latitude: float, places: int, expected: tuple[float, float]
) -> None:
    point = Coordinates(longitude=longitude, latitude=latitude)

    rounded = round_coordinates(point, places)

    assert (rounded.longitude, rounded.latitude) == expected


@pytest.mark.parametrize("places", [-1, MAX_PUBLIC_DECIMALS + 1])
def test_round_coordinates_decimals_out_of_range_raises_validation_error(
    places: int,
) -> None:
    point = Coordinates(longitude=74.0, latitude=36.0)

    with pytest.raises(ValidationError, match="decimals must be between"):
        round_coordinates(point, places)


@given(point=coordinates, places=decimals)
def test_round_coordinates_any_point_stays_in_bounds_and_is_idempotent(
    point: Coordinates, places: int
) -> None:
    rounded = round_coordinates(point, places)

    again = round_coordinates(rounded, places)

    assert -180 <= rounded.longitude <= 180
    assert -90 <= rounded.latitude <= 90
    assert again == rounded


@given(point=coordinates, places=decimals)
def test_round_coordinates_any_point_moves_at_most_half_a_unit_per_axis(
    point: Coordinates, places: int
) -> None:
    half_unit = 0.5 * 10**-places + FLOAT_TOLERANCE_DEGREES

    rounded = round_coordinates(point, places)

    assert abs(rounded.longitude - point.longitude) <= half_unit
    assert abs(rounded.latitude - point.latitude) <= half_unit


@given(point=regional_coordinates)
def test_round_coordinates_two_decimals_in_region_moves_under_documented_bound(
    point: Coordinates,
) -> None:
    rounded = round_coordinates(point, 2)

    north_south_only = Coordinates(longitude=point.longitude, latitude=rounded.latitude)

    assert _haversine_km(point, rounded) <= TWO_DECIMALS_BOUND_KM
    assert _haversine_km(point, north_south_only) <= NORTH_SOUTH_BOUND_KM


@given(point=coordinates, places=decimals)
def test_public_coordinate_policy_apply_matches_round_coordinates(
    point: Coordinates, places: int
) -> None:
    policy = PublicCoordinatePolicy(decimals=places)

    published = policy.apply(point)

    assert published == round_coordinates(point, places)


@pytest.mark.parametrize("places", [-1, 7, True, "2"])
def test_public_coordinate_policy_invalid_decimals_raises_validation_error(
    places: object,
) -> None:
    with pytest.raises(PydanticValidationError):
        PublicCoordinatePolicy.model_validate({"decimals": places})


def test_public_coordinate_policy_is_frozen() -> None:
    policy = PublicCoordinatePolicy(decimals=2)

    with pytest.raises(PydanticValidationError):
        policy.decimals = 3  # type: ignore[misc]  # reason: frozen model
