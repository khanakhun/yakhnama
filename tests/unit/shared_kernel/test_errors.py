"""Unit tests for ``yakhnama.shared_kernel.errors``."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from yakhnama.shared_kernel.errors import (
    AuthenticationError,
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    PreconditionRequiredError,
    ValidationError,
    YakhnamaError,
)

SUBCLASSES_AND_CODES = [
    (NotFoundError, "not_found"),
    (ConflictError, "conflict"),
    (ValidationError, "validation_error"),
    (PermissionDeniedError, "permission_denied"),
    (InvariantViolationError, "invariant_violation"),
    (InvalidTransitionError, "invalid_transition"),
    (PreconditionFailedError, "precondition_failed"),
    (PreconditionRequiredError, "precondition_required"),
    (AuthenticationError, "authentication_failed"),
]


@given(message=st.text(max_size=200))
def test_yakhnama_error_str_any_message_returns_message(message: str) -> None:
    error = YakhnamaError(message, details={"field": "name"})

    result = str(error)

    assert result == message
    assert error.message == message


def test_yakhnama_error_without_details_has_empty_details() -> None:
    error = YakhnamaError("boom")

    details = error.details

    assert dict(details) == {}
    assert YakhnamaError.code == "yakhnama_error"


def test_yakhnama_error_caller_mutates_details_error_keeps_original() -> None:
    details: dict[str, object] = {"field": "name"}
    error = YakhnamaError("boom", details=details)

    details["field"] = "changed"

    assert error.details["field"] == "name"


def test_yakhnama_error_details_assignment_raises_type_error() -> None:
    error = YakhnamaError("boom", details={"field": "name"})

    with pytest.raises(TypeError):
        error.details["field"] = "changed"  # type: ignore[index]  # reason: proves the mapping is read-only at runtime


@pytest.mark.parametrize(("error_type", "code"), SUBCLASSES_AND_CODES)
def test_error_subclass_raised_is_caught_as_yakhnama_error_with_code(
    error_type: type[YakhnamaError], code: str
) -> None:
    message = "failed"

    with pytest.raises(YakhnamaError) as caught:
        raise error_type(message)

    assert caught.value.code == code
    assert isinstance(caught.value, error_type)


def test_error_codes_all_classes_are_unique() -> None:
    codes = [code for _, code in SUBCLASSES_AND_CODES] + [YakhnamaError.code]

    unique = set(codes)

    assert len(unique) == len(codes)
