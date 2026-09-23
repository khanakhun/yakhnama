"""Unit tests for ``yakhnama.modules.provenance.domain.errors``."""

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.provenance.domain.errors import (
    InvalidSourceUrlError,
    SourceImmutableError,
    SourceNotFoundError,
)
from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)

IDS = SequentialIdGenerator()


def test_source_not_found_error_for_id_is_not_found_with_id_detail() -> None:
    source_id = IDS.new_id()

    error = SourceNotFoundError.for_id(source_id)

    assert isinstance(error, NotFoundError)
    assert error.code == "not_found"
    assert error.details == {"source_id": str(source_id)}


def test_source_immutable_error_for_id_is_invalid_transition_with_id_detail() -> None:
    source_id = IDS.new_id()

    error = SourceImmutableError.for_id(source_id)

    assert isinstance(error, InvalidTransitionError)
    assert error.code == "invalid_transition"
    assert error.details == {"source_id": str(source_id)}
    assert str(source_id) not in error.message


def test_invalid_source_url_error_because_is_validation_error_with_reason() -> None:
    error = InvalidSourceUrlError.because("url must have a host")

    assert isinstance(error, ValidationError)
    assert error.message == "invalid source url"
    assert error.details == {"reason": "url must have a host"}
