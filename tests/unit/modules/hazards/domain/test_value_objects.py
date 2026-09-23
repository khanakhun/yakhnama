"""Unit tests for ``yakhnama.modules.hazards.domain.value_objects``."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.hazards.domain.value_objects import (
    GLIMS_ID_PATTERN,
    GlacialLakeRef,
    GlacierRef,
    HazardCode,
    HazardTypeRef,
    IrdrAlignment,
    RetirementReason,
)

HAZARD_CODE_ADAPTER: TypeAdapter[str] = TypeAdapter(HazardCode)
VALID_CODES = st.from_regex(r"\A[a-z][a-z0-9_]{1,63}\Z")


@given(code=VALID_CODES)
def test_hazard_code_matching_pattern_is_accepted(code: str) -> None:
    result = HAZARD_CODE_ADAPTER.validate_python(code)

    assert result == code


@pytest.mark.parametrize(
    "code", ["", "g", "Glof", "1glof", "glof-lake", "glof lake", "a" * 65, "_glof"]
)
def test_hazard_code_off_pattern_raises_validation_error(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        HAZARD_CODE_ADAPTER.validate_python(code)


def test_hazard_type_ref_is_frozen_and_rejects_extra_fields() -> None:
    ref = HazardTypeRef(code="glof")

    with pytest.raises(PydanticValidationError):
        ref.code = "flood"  # type: ignore[misc]  # reason: proves the frozen model rejects assignment

    with pytest.raises(PydanticValidationError):
        HazardTypeRef.model_validate({"code": "glof", "label": "GLOF"})


@pytest.mark.parametrize(
    "family",
    [
        "geophysical",
        "hydrological",
        "meteorological",
        "climatological",
        "extraterrestrial",
        "biological",
    ],
)
def test_irdr_alignment_known_family_is_accepted(family: str) -> None:
    alignment = IrdrAlignment.model_validate({"family": family, "main_event": "X"})

    assert alignment.family == family
    assert alignment.peril is None


@pytest.mark.parametrize(
    "fields",
    [
        {"family": "Hydrological", "main_event": "Flood"},
        {"family": "cryospheric", "main_event": "Flood"},
        {"family": "hydrological", "main_event": ""},
        {"family": "hydrological", "main_event": "x" * 65},
        {"family": "hydrological", "main_event": "Flood", "peril": "   "},
    ],
)
def test_irdr_alignment_invalid_fields_raise_validation_error(
    fields: dict[str, str],
) -> None:
    with pytest.raises(PydanticValidationError):
        IrdrAlignment.model_validate(fields)


def test_irdr_alignment_terms_are_stripped() -> None:
    alignment = IrdrAlignment(
        family="hydrological", main_event=" Flood ", peril=" Flash flood "
    )

    result = (alignment.main_event, alignment.peril)

    assert result == ("Flood", "Flash flood")


@given(glims_id=st.from_regex(GLIMS_ID_PATTERN.replace("^", r"\A").replace("$", r"\Z")))
def test_glacier_ref_id_matching_glims_pattern_is_accepted(glims_id: str) -> None:
    glacier = GlacierRef(glims_id=glims_id)

    assert glacier.glims_id == glims_id


@pytest.mark.parametrize(
    "glims_id",
    ["", "G074567E36234", "g074567E36234N", "G74567E36234N", "G074567E36234X"],
)
def test_glacier_ref_malformed_id_raises_validation_error(glims_id: str) -> None:
    with pytest.raises(PydanticValidationError):
        GlacierRef(glims_id=glims_id)


def test_glacial_lake_ref_known_inventory_keeps_id_verbatim_after_strip() -> None:
    lake = GlacialLakeRef(inventory="icimod", inventory_id=" GL-123 ")

    assert lake.inventory_id == "GL-123"


@pytest.mark.parametrize(
    "fields",
    [
        {"inventory": "ICIMOD", "inventory_id": "1"},
        {"inventory": "icimod", "inventory_id": ""},
        {"inventory": "icimod", "inventory_id": "x" * 65},
    ],
)
def test_glacial_lake_ref_invalid_fields_raise_validation_error(
    fields: dict[str, str],
) -> None:
    with pytest.raises(PydanticValidationError):
        GlacialLakeRef.model_validate(fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"text": ""},
        {"text": "x" * 501},
        {"text": "merged", "replaced_by": "Not A Code"},
    ],
)
def test_retirement_reason_invalid_fields_raise_validation_error(
    fields: dict[str, str],
) -> None:
    with pytest.raises(PydanticValidationError):
        RetirementReason.model_validate(fields)


def test_retirement_reason_without_replacement_defaults_to_none() -> None:
    reason = RetirementReason(text="duplicate of flood")

    assert reason.replaced_by is None
