"""Unit tests for ``yakhnama.modules.geography.domain.errors``."""

import pytest

from yakhnama.modules.geography.domain.errors import (
    DuplicatePlaceNameError,
    InvalidPlaceHierarchyError,
    PlaceNameNotFoundError,
    PlaceNotFoundError,
    PlaceRetiredError,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    YakhnamaError,
)
from yakhnama.shared_kernel.ids import Uuid7Generator


@pytest.mark.parametrize(
    ("error_class", "family"),
    [
        (PlaceNotFoundError, NotFoundError),
        (PlaceNameNotFoundError, NotFoundError),
        (DuplicatePlaceNameError, ConflictError),
        (InvalidPlaceHierarchyError, InvariantViolationError),
        (PlaceRetiredError, InvalidTransitionError),
    ],
)
def test_geography_error_belongs_to_kernel_family(
    error_class: type[YakhnamaError], family: type[YakhnamaError]
) -> None:
    error = error_class("message")

    assert isinstance(error, family)
    assert error.code == family.code


def test_place_not_found_for_id_carries_id_in_details() -> None:
    place_id = Uuid7Generator().new_id()

    error = PlaceNotFoundError.for_id(place_id)

    assert error.details == {"place_id": str(place_id)}
    assert str(place_id) in error.message


def test_place_not_found_for_code_carries_code_in_details() -> None:
    error = PlaceNotFoundError.for_code("pk.gb.hunza")

    assert error.details == {"code": "pk.gb.hunza"}
    assert "'pk.gb.hunza'" in str(error)
