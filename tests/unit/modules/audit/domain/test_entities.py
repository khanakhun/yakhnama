"""Unit tests for ``yakhnama.modules.audit.domain.entities``."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.audit import AuditEntryTestFactory
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.audit.domain.entities import AuditEntry

IDS = SequentialIdGenerator()


def _entry(**fields: object) -> AuditEntry:
    return AuditEntryTestFactory.build(factory_use_construct=False, **fields)


def test_audit_entry_occurred_at_in_other_offset_is_normalised_to_utc() -> None:
    local = datetime(2026, 9, 1, 5, tzinfo=timezone(timedelta(hours=5)))

    entry = _entry(occurred_at=local)

    assert entry.occurred_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert entry.occurred_at.tzinfo is UTC


def test_audit_entry_naive_occurred_at_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _entry(occurred_at=datetime(2026, 9, 1))  # noqa: DTZ001  # reason: the naive value under test


def test_audit_entry_user_actor_with_id_is_accepted() -> None:
    actor_id = IDS.new_id()

    entry = _entry(actor_kind="user", actor_id=actor_id)

    assert entry.actor_id == actor_id


@pytest.mark.parametrize(
    ("actor_kind", "actor_id"), [("user", None), ("system", IDS.new_id())]
)
def test_audit_entry_actor_kind_inconsistent_with_actor_id_is_rejected(
    actor_kind: str, actor_id: UUID | None
) -> None:
    with pytest.raises(PydanticValidationError, match="actor_kind"):
        _entry(actor_kind=actor_kind, actor_id=actor_id)


def test_audit_entry_unknown_actor_kind_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _entry(actor_kind="service")


def test_audit_entry_attribute_assignment_is_refused() -> None:
    entry = _entry()

    with pytest.raises(PydanticValidationError):
        entry.action = "audit.tampered"  # type: ignore[misc]  # reason: asserting frozen


def test_audit_entry_exposes_no_state_changing_methods() -> None:
    own_callables = {
        name
        for name, member in vars(AuditEntry).items()
        if callable(member) and not name.startswith("_")
    }

    assert own_callables == set()
