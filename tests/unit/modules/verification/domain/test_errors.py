"""Unit tests for ``yakhnama.modules.verification.domain.errors``."""

from tests.factories.verification import VerificationTargetTestFactory
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.verification.domain.errors import (
    CaseAlreadyOpenError,
    HumanRequiredError,
    ReasonRequiredError,
    VerificationCaseNotFoundError,
)
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)

IDS = SequentialIdGenerator(seed=13)


def test_verification_case_not_found_error_carries_case_id() -> None:
    case_id = IDS.new_id()

    error = VerificationCaseNotFoundError(case_id)

    assert isinstance(error, NotFoundError)
    assert error.code == "not_found"
    assert error.case_id == case_id
    assert error.details == {"case_id": str(case_id)}
    assert str(case_id) in str(error)


def test_reason_required_error_is_validation_error_naming_state() -> None:
    error = ReasonRequiredError(VerificationState.REJECTED)

    assert isinstance(error, ValidationError)
    assert error.to_state is VerificationState.REJECTED
    assert error.details == {"to_state": "rejected"}


def test_human_required_error_is_permission_denied_naming_state() -> None:
    error = HumanRequiredError(VerificationState.VERIFIED)

    assert isinstance(error, PermissionDeniedError)
    assert error.code == "permission_denied"
    assert error.details == {"to_state": "verified"}


def test_case_already_open_error_is_conflict_with_target_and_case() -> None:
    target = VerificationTargetTestFactory.build(kind=TargetKind.REPORT)
    case_id = IDS.new_id()

    error = CaseAlreadyOpenError(target, case_id)

    assert isinstance(error, ConflictError)
    assert error.details == {
        "target_kind": "report",
        "target_id": str(target.target_id),
        "case_id": str(case_id),
    }
    assert error.target == target
    assert error.case_id == case_id
