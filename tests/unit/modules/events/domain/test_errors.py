"""Unit tests for ``yakhnama.modules.events.domain.errors``."""

from tests.unit.modules.events.domain.samples import ids
from yakhnama.modules.events.domain.errors import (
    AttributesMismatchError,
    EventImmutableError,
    EventNotFoundError,
    InvalidEventStatusError,
    InvalidRelationError,
    ReportAlreadyLinkedError,
    ReportNotLinkedError,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)

EVENT_ID = ids(seed=90).new_id()
REPORT_ID = ids(seed=91).new_id()


def test_event_not_found_error_is_not_found_with_id() -> None:
    error = EventNotFoundError(EVENT_ID)

    assert isinstance(error, NotFoundError)
    assert error.event_id == EVENT_ID
    assert error.details == {"event_id": str(EVENT_ID)}


def test_event_immutable_error_is_invalid_transition_with_context() -> None:
    error = EventImmutableError(EVENT_ID, "retracted", "publish")

    assert isinstance(error, InvalidTransitionError)
    assert (error.status, error.operation) == ("retracted", "publish")
    assert error.details["event_id"] == str(EVENT_ID)


def test_invalid_event_status_error_is_invalid_transition_with_context() -> None:
    error = InvalidEventStatusError(EVENT_ID, "published", "publish")

    assert isinstance(error, InvalidTransitionError)
    assert error.details == {
        "event_id": str(EVENT_ID),
        "status": "published",
        "operation": "publish",
    }
    assert error.event_id == EVENT_ID
    assert (error.status, error.operation) == ("published", "publish")


def test_report_link_errors_carry_both_ids() -> None:
    already = ReportAlreadyLinkedError(EVENT_ID, REPORT_ID)
    missing = ReportNotLinkedError(EVENT_ID, REPORT_ID)

    assert isinstance(already, ConflictError)
    assert isinstance(missing, NotFoundError)
    for error in (already, missing):
        assert error.details == {
            "event_id": str(EVENT_ID),
            "report_id": str(REPORT_ID),
        }
        assert (error.event_id, error.report_id) == (EVENT_ID, REPORT_ID)


def test_invalid_relation_error_is_invariant_violation() -> None:
    error = InvalidRelationError("cycle")

    assert isinstance(error, InvariantViolationError)
    assert error.code == "invariant_violation"


def test_attributes_mismatch_error_is_validation_error_with_codes() -> None:
    error = AttributesMismatchError("glof", "landslide")

    assert isinstance(error, ValidationError)
    assert error.details == {"hazard_code": "glof", "attributes_code": "landslide"}
