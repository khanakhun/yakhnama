"""Unit tests for ``yakhnama.modules.hazards.domain.errors``."""

import pytest

from yakhnama.modules.hazards.domain.errors import (
    HazardAttributesRegistrationError,
    HazardCodeAlreadyUsedError,
    HazardTypeNotFoundError,
    HazardTypeNotRetiredError,
    HazardTypeRetiredError,
    InvalidTaxonomyError,
    UnknownHazardAttributesError,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
    YakhnamaError,
)


@pytest.mark.parametrize(
    ("error", "family", "details"),
    [
        (HazardTypeNotFoundError("glof"), NotFoundError, {"hazard_code": "glof"}),
        (HazardCodeAlreadyUsedError("glof"), ConflictError, {"hazard_code": "glof"}),
        (
            HazardTypeRetiredError("glof", "retire"),
            InvalidTransitionError,
            {"hazard_code": "glof", "action": "retire"},
        ),
        (
            HazardTypeNotRetiredError("glof"),
            InvalidTransitionError,
            {"hazard_code": "glof"},
        ),
        (
            UnknownHazardAttributesError("lava"),
            ValidationError,
            {"schema_code": "lava"},
        ),
        (InvalidTaxonomyError("cycle"), InvariantViolationError, {}),
        (HazardAttributesRegistrationError("twice"), InvariantViolationError, {}),
    ],
)
def test_hazards_error_construction_belongs_to_kernel_family_with_details(
    error: YakhnamaError, family: type[YakhnamaError], details: dict[str, str]
) -> None:
    result = dict(error.details)

    assert isinstance(error, family)
    assert result == details
    assert str(error)


def test_hazard_type_retired_error_attributes_name_code_and_action() -> None:
    error = HazardTypeRetiredError("glof", "reparent")

    message = str(error)

    assert (error.hazard_code, error.action) == ("glof", "reparent")
    assert "reparent" in message


def test_code_carrying_errors_expose_the_code_as_attribute() -> None:
    errors = (
        HazardTypeNotFoundError("a_code"),
        HazardCodeAlreadyUsedError("a_code"),
        HazardTypeNotRetiredError("a_code"),
    )

    codes = {error.hazard_code for error in errors}

    assert codes == {"a_code"}
    assert UnknownHazardAttributesError("a_code").schema_code == "a_code"
