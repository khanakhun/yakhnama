"""Unit tests for ``yakhnama.modules.audit.domain.errors``."""

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.audit.domain.errors import (
    AuditEntryImmutableError,
    AuditEntryNotFoundError,
)
from yakhnama.shared_kernel.errors import InvariantViolationError, NotFoundError

IDS = SequentialIdGenerator()


def test_audit_entry_not_found_error_for_id_is_not_found_with_id() -> None:
    entry_id = IDS.new_id()

    error = AuditEntryNotFoundError.for_id(entry_id)

    assert isinstance(error, NotFoundError)
    assert error.details == {"audit_entry_id": str(entry_id)}


def test_audit_entry_immutable_error_for_id_is_invariant_violation_with_id() -> None:
    entry_id = IDS.new_id()

    error = AuditEntryImmutableError.for_id(entry_id)

    assert isinstance(error, InvariantViolationError)
    assert error.code == "invariant_violation"
    assert error.details == {"audit_entry_id": str(entry_id)}
