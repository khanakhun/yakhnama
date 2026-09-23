"""Unit tests for ``yakhnama.modules.hazards.domain.attributes``."""

from datetime import UTC, datetime
from typing import Literal, get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.hazards.domain.attributes import (
    DEFAULT_REGISTRY,
    HAZARD_ATTRIBUTES_ADAPTER,
    AvalancheAttributes,
    GlacierSurgeAttributes,
    GlofAttributes,
    HazardAttributeRegistry,
    HazardAttributes,
    HazardAttributesUnion,
    LandslideAttributes,
)
from yakhnama.modules.hazards.domain.errors import (
    HazardAttributesRegistrationError,
    UnknownHazardAttributesError,
)
from yakhnama.shared_kernel.value_objects import DatePrecision, Measurement

SEVEN_CODES = frozenset(
    {
        "glof",
        "landslide",
        "debris_flow",
        "cloudburst",
        "flash_flood",
        "avalanche",
        "glacier_surge",
    }
)
QUANTITIES = st.floats(min_value=0, max_value=1e12, allow_nan=False)


def _discharge(
    amount: float, unit: str = "cubic_metre_per_second"
) -> dict[str, object]:
    return {"value": amount, "unit": unit}


def test_default_registry_codes_are_the_seven_proposed_hazards() -> None:
    codes = DEFAULT_REGISTRY.codes()

    assert codes == SEVEN_CODES


def test_default_registry_schemas_match_union_members() -> None:
    union_members = set(get_args(get_args(HazardAttributesUnion)[0]))

    registered = {
        DEFAULT_REGISTRY.schema_for(code) for code in DEFAULT_REGISTRY.codes()
    }

    assert registered == union_members


@pytest.mark.parametrize("code", sorted(SEVEN_CODES))
def test_registry_validate_empty_payload_defaults_every_field(code: str) -> None:
    attributes = DEFAULT_REGISTRY.validate(code, {})

    assert attributes.hazard_type == code
    assert isinstance(attributes, DEFAULT_REGISTRY.schema_for(code))


@pytest.mark.parametrize("code", sorted(SEVEN_CODES))
def test_registry_validate_unknown_field_raises_validation_error(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate(code, {"magnitude": 3})


@pytest.mark.parametrize("code", sorted(SEVEN_CODES))
def test_registry_validate_other_discriminator_raises_validation_error(
    code: str,
) -> None:
    other = "glof" if code != "glof" else "landslide"

    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate(code, {"hazard_type": other})


def test_registry_validate_unknown_code_raises_unknown_hazard_attributes() -> None:
    with pytest.raises(UnknownHazardAttributesError) as caught:
        DEFAULT_REGISTRY.validate("lava_flow", {})

    assert caught.value.schema_code == "lava_flow"


def test_registry_schema_for_unknown_code_raises_unknown_hazard_attributes() -> None:
    with pytest.raises(UnknownHazardAttributesError):
        DEFAULT_REGISTRY.schema_for("glof_v2")


def test_union_unknown_discriminator_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        HAZARD_ATTRIBUTES_ADAPTER.validate_python({"hazard_type": "lava_flow"})


def test_union_missing_discriminator_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        HAZARD_ATTRIBUTES_ADAPTER.validate_python({})


@given(
    discharge=QUANTITIES,
    volume=QUANTITIES,
    area_before=QUANTITIES,
    area_after=QUANTITIES,
)
def test_glof_attributes_full_payload_round_trips_through_union_json(
    discharge: float, volume: float, area_before: float, area_after: float
) -> None:
    attributes = DEFAULT_REGISTRY.validate(
        "glof",
        {
            "source_lake": {"inventory": "icimod", "inventory_id": "GL-1"},
            "source_glacier": {"glims_id": "G074567E36234N"},
            "mechanism": "moraine_dam_breach",
            "peak_discharge": _discharge(discharge),
            "flood_volume": {"value": volume, "unit": "cubic_metre"},
            "lake_area_before": {"value": area_before, "unit": "square_metre"},
            "lake_area_after": {"value": area_after, "unit": "square_metre"},
        },
    )

    result = HAZARD_ATTRIBUTES_ADAPTER.validate_json(attributes.model_dump_json())

    assert result == attributes
    assert isinstance(result, GlofAttributes)


@pytest.mark.parametrize("code", sorted(SEVEN_CODES))
def test_every_schema_minimal_instance_round_trips_through_union_json(
    code: str,
) -> None:
    attributes = DEFAULT_REGISTRY.validate(code, {})

    result = HAZARD_ATTRIBUTES_ADAPTER.validate_json(attributes.model_dump_json())

    assert result == attributes


@pytest.mark.parametrize(
    ("code", "field", "value"),
    [
        ("glof", "flood_volume", {"value": 1.0, "unit": "square_metre"}),
        ("glof", "lake_area_before", {"value": 1.0, "unit": "cubic_metre"}),
        ("glof", "peak_discharge", _discharge(1.0, unit="cubic_metre")),
        ("landslide", "volume", {"value": 1.0, "unit": "metre"}),
        ("landslide", "runout_length", {"value": 1.0, "unit": "second"}),
        ("debris_flow", "volume", {"value": 1.0, "unit": "kilogram"}),
        ("cloudburst", "rainfall_total", {"value": 1.0, "unit": "cubic_metre"}),
        ("cloudburst", "duration", {"value": 1.0, "unit": "metre"}),
        ("cloudburst", "peak_intensity", _discharge(1.0)),
        ("cloudburst", "peak_intensity", {"value": 1.0, "unit": "metre"}),
        ("flash_flood", "peak_discharge", _discharge(1.0, unit="metre_per_second")),
        ("glacier_surge", "advance_distance", {"value": 1.0, "unit": "square_metre"}),
    ],
)
def test_measurement_in_wrong_unit_raises_validation_error(
    code: str, field: str, value: dict[str, object]
) -> None:
    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate(code, {field: value})


def test_measurement_in_unknown_unit_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate(
            "glof", {"flood_volume": {"value": 1.0, "unit": "acre_foot"}}
        )


@pytest.mark.parametrize(
    ("code", "field", "value"),
    [
        ("glof", "flood_volume", {"value": -1.0, "unit": "cubic_metre"}),
        ("glof", "peak_discharge", _discharge(-1.0)),
        ("cloudburst", "peak_intensity", {"value": -1e-5, "unit": "metre_per_second"}),
        ("landslide", "runout_length", {"value": -0.1, "unit": "metre"}),
        ("cloudburst", "duration", {"value": -5.0, "unit": "second"}),
    ],
)
def test_negative_measurement_raises_validation_error(
    code: str, field: str, value: dict[str, object]
) -> None:
    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate(code, {field: value})


@given(intensity=st.floats(min_value=0, max_value=1.0, allow_nan=False))
def test_cloudburst_intensity_in_metre_per_second_is_accepted(
    intensity: float,
) -> None:
    payload = {"peak_intensity": {"value": intensity, "unit": "metre_per_second"}}

    attributes = DEFAULT_REGISTRY.validate("cloudburst", payload)

    assert attributes.model_dump()["peak_intensity"] == payload["peak_intensity"]


@pytest.mark.parametrize("size_class", [1, 5])
def test_avalanche_size_class_within_one_to_five_is_accepted(size_class: int) -> None:
    attributes = DEFAULT_REGISTRY.validate("avalanche", {"size_class": size_class})

    assert isinstance(attributes, AvalancheAttributes)
    assert attributes.size_class == size_class


@pytest.mark.parametrize("size_class", [0, 6])
def test_avalanche_size_class_outside_one_to_five_raises_validation_error(
    size_class: int,
) -> None:
    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate("avalanche", {"size_class": size_class})


@pytest.mark.parametrize(
    ("code", "field", "value"),
    [
        ("glof", "mechanism", "earthquake"),
        ("landslide", "movement_type", "slump"),
        ("landslide", "material", "ice"),
        ("landslide", "trigger", "glof"),
        ("debris_flow", "trigger", "human"),
        ("flash_flood", "trigger", "rainfall"),
        ("avalanche", "avalanche_type", "powder"),
        ("avalanche", "trigger", "explosive"),
    ],
)
def test_enumerated_field_unlisted_value_raises_validation_error(
    code: str, field: str, value: str
) -> None:
    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate(code, {field: value})


def test_landslide_attributes_listed_values_are_kept() -> None:
    attributes = DEFAULT_REGISTRY.validate(
        "landslide",
        {"movement_type": "slide", "material": "rock", "trigger": "earthquake"},
    )

    assert attributes == LandslideAttributes(
        movement_type="slide", material="rock", trigger="earthquake"
    )


def _surge(start: datetime, end: datetime) -> dict[str, object]:
    return {
        "surge_start": {"value": start, "precision": DatePrecision.MONTH},
        "surge_end": {"value": end, "precision": DatePrecision.DAY},
    }


def test_glacier_surge_end_before_start_raises_validation_error() -> None:
    payload = _surge(datetime(2022, 5, 1, tzinfo=UTC), datetime(2022, 4, 1, tzinfo=UTC))

    with pytest.raises(PydanticValidationError):
        DEFAULT_REGISTRY.validate("glacier_surge", payload)


def test_glacier_surge_end_after_start_is_accepted() -> None:
    payload = _surge(datetime(2022, 4, 1, tzinfo=UTC), datetime(2022, 5, 1, tzinfo=UTC))

    attributes = DEFAULT_REGISTRY.validate("glacier_surge", payload)

    assert isinstance(attributes, GlacierSurgeAttributes)
    assert attributes.surge_end is not None


def test_glacier_surge_start_only_is_accepted() -> None:
    attributes = GlacierSurgeAttributes.model_validate(
        {
            "surge_start": {
                "value": datetime(2022, 4, 1, tzinfo=UTC),
                "precision": "month",
            }
        }
    )

    assert attributes.surge_end is None


def test_attributes_are_frozen() -> None:
    attributes = GlofAttributes(flood_volume=Measurement(value=1.0, unit="cubic_metre"))

    with pytest.raises(PydanticValidationError):
        attributes.mechanism = "overtopping"  # type: ignore[misc]  # reason: proves the frozen model rejects assignment


class _LavaFlowAttributes(HazardAttributes):
    """Schema for a hazard outside the default registry.

    Implements: Strategy.
    """

    hazard_type: Literal["lava_flow"] = "lava_flow"


class _MislabelledAttributes(HazardAttributes):
    """Schema whose default disagrees with its literal.

    Implements: Strategy.
    """

    hazard_type: Literal["mud_flow"] = "mud_flow"


def test_registry_register_returns_new_registry_and_keeps_original() -> None:
    registry = HazardAttributeRegistry()

    extended = registry.register("lava_flow", _LavaFlowAttributes)

    assert extended.codes() == {"lava_flow"}
    assert registry.codes() == frozenset()
    assert isinstance(extended.validate("lava_flow", {}), _LavaFlowAttributes)


def test_registry_register_duplicate_code_raises_registration_error() -> None:
    with pytest.raises(HazardAttributesRegistrationError):
        DEFAULT_REGISTRY.register("glof", GlofAttributes)


@pytest.mark.parametrize(
    ("code", "schema"),
    [
        ("glof_v2", GlofAttributes),
        ("lava", _MislabelledAttributes),
        ("anything", HazardAttributes),
    ],
)
def test_registry_register_mismatched_discriminator_raises_registration_error(
    code: str, schema: type[HazardAttributes]
) -> None:
    with pytest.raises(HazardAttributesRegistrationError):
        HazardAttributeRegistry().register(code, schema)
