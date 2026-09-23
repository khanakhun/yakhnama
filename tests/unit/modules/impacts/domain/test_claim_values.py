"""Unit tests for the Phase 3 value objects in ``impacts.domain.value_objects``."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.impacts.domain.value_objects import (
    SOURCE_TYPE_NAMES,
    AssetKind,
    AssetName,
    ClaimNote,
    ClaimScope,
    ClaimStatus,
    ClaimValue,
    CountValue,
    DamageLevel,
    MeasurementValue,
    MonetaryValue,
    OsmId,
    RetractionReason,
    SourceRank,
    ValueKind,
)
from yakhnama.shared_kernel.ids import Uuid7Generator
from yakhnama.shared_kernel.value_objects import Measurement

IDS = Uuid7Generator()
CLAIM_VALUE = TypeAdapter[ClaimValue](ClaimValue)
OSM_ID = TypeAdapter[str](OsmId)

# The provenance module's ``SourceType`` values, pinned here because the impacts
# domain mirrors them instead of importing provenance.
PROVENANCE_SOURCE_TYPES = (
    "citizen",
    "organisation",
    "government",
    "news",
    "satellite",
    "research",
    "dataset",
)


@given(count=st.integers(min_value=0, max_value=10**12))
def test_count_value_non_negative_count_round_trips_as_count_kind(count: int) -> None:
    value = CountValue(count=count)

    restored = CLAIM_VALUE.validate_json(CLAIM_VALUE.dump_json(value))

    assert (restored, value.value_kind, value.unit_or_currency) == (
        value,
        ValueKind.COUNT,
        "count",
    )


@given(count=st.integers(max_value=-1))
def test_count_value_negative_count_is_rejected(count: int) -> None:
    with pytest.raises(PydanticValidationError):
        CountValue(count=count)


@given(quantity=st.floats(min_value=0, max_value=1e12, allow_nan=False))
def test_measurement_value_non_negative_quantity_round_trips(quantity: float) -> None:
    value = MeasurementValue(
        measurement=Measurement(value=quantity, unit="square_metre")
    )

    restored = CLAIM_VALUE.validate_json(CLAIM_VALUE.dump_json(value))

    assert (restored, value.value_kind, value.unit_or_currency) == (
        value,
        ValueKind.MEASUREMENT,
        "square_metre",
    )


@pytest.mark.parametrize(
    ("measurement", "fragment"),
    [
        (Measurement(value=-0.5, unit="metre"), "non-negative"),
        (Measurement(value=3, unit="count"), "unit 'count'"),
    ],
)
def test_measurement_value_negative_or_count_unit_is_rejected(
    measurement: Measurement, fragment: str
) -> None:
    with pytest.raises(PydanticValidationError, match=fragment):
        MeasurementValue(measurement=measurement)


def test_monetary_value_valid_amount_round_trips_as_monetary_kind() -> None:
    value = MonetaryValue(amount=Decimal("1250000.50"), currency="PKR", price_year=2022)

    restored = CLAIM_VALUE.validate_json(CLAIM_VALUE.dump_json(value))

    assert (restored, value.value_kind, value.unit_or_currency) == (
        value,
        ValueKind.MONETARY,
        "PKR",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": Decimal(-1)},
        {"amount": Decimal("0.001")},
        {"amount": Decimal("NaN")},
        {"amount": Decimal(10) ** 20},
        {"currency": "pkr"},
        {"price_year": 1899},
        {"price_year": 2101},
    ],
)
def test_monetary_value_out_of_bounds_field_is_rejected(
    overrides: dict[str, object],
) -> None:
    fields: dict[str, object] = {
        "amount": Decimal(1),
        "currency": "PKR",
        "price_year": 2022,
    }
    fields.update(overrides)

    with pytest.raises(PydanticValidationError):
        MonetaryValue.model_validate(fields)


def test_claim_value_unknown_kind_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        CLAIM_VALUE.validate_python({"kind": "percentage", "count": 3})


def test_claim_status_values_are_active_and_retracted() -> None:
    assert [status.value for status in ClaimStatus] == ["active", "retracted"]


def test_source_type_names_mirror_provenance_source_types() -> None:
    assert set(SOURCE_TYPE_NAMES) == set(PROVENANCE_SOURCE_TYPES)


def test_source_rank_orders_types_as_proposed() -> None:
    ranked = sorted(SOURCE_TYPE_NAMES, key=SourceRank.of, reverse=True)

    assert ranked == [
        "government",
        "research",
        "satellite",
        "dataset",
        "organisation",
        "news",
        "citizen",
    ]


def test_claim_scope_default_covers_whole_event() -> None:
    scope = ClaimScope()

    assert scope.is_whole_event is True


@pytest.mark.parametrize(
    "fields", [{"place_code": "pk.gb.hunza"}, {"asset_id": IDS.new_id()}]
)
def test_claim_scope_with_place_or_asset_is_narrower_than_event(
    fields: dict[str, object],
) -> None:
    scope = ClaimScope.model_validate(fields)

    assert scope.is_whole_event is False


def test_claim_scope_equal_fields_are_equal_and_hashable() -> None:
    asset_id = IDS.new_id()

    first = ClaimScope(place_code="pk.gb.hunza", asset_id=asset_id)
    second = ClaimScope(place_code="pk.gb.hunza", asset_id=asset_id)

    assert (first == second, hash(first) == hash(second)) == (True, True)


def test_claim_scope_malformed_place_code_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ClaimScope(place_code="Hunza Valley")


@given(
    element=st.sampled_from(["node", "way", "relation"]),
    number=st.integers(min_value=1, max_value=10**18),
)
def test_osm_id_element_and_positive_number_is_accepted(
    element: str, number: int
) -> None:
    candidate = f"{element}/{number}"

    assert OSM_ID.validate_python(candidate) == candidate


@pytest.mark.parametrize("candidate", ["way/0", "way/01", "area/12", "way/", "12"])
def test_osm_id_malformed_value_is_rejected(candidate: str) -> None:
    with pytest.raises(PydanticValidationError):
        OSM_ID.validate_python(candidate)


def test_asset_kinds_and_damage_levels_are_the_proposed_lists() -> None:
    assert (
        [kind.value for kind in AssetKind],
        [level.value for level in DamageLevel],
    ) == (
        ["bridge", "road_segment", "water_channel", "power_line", "building", "other"],
        ["damaged", "destroyed", "washed_away"],
    )


@pytest.mark.parametrize(
    ("alias", "candidate", "is_valid"),
    [
        (AssetName, "  Passu bridge ", True),
        (AssetName, "a" * 201, False),
        (AssetName, "line\nbreak", False),
        (AssetName, "evil" + chr(0x202E) + "txt", False),
        (RetractionReason, "", False),
        (RetractionReason, "duplicate of the NDMA figure", True),
        (ClaimNote, "first line\r\nsecond line", True),
        (ClaimNote, "nul" + chr(0), False),
    ],
)
def test_free_text_types_apply_safe_text_rules(
    alias: object, candidate: str, *, is_valid: bool
) -> None:
    adapter = TypeAdapter[str](alias)

    try:
        adapter.validate_python(candidate)
        accepted = True
    except PydanticValidationError:
        accepted = False

    assert accepted is is_valid
