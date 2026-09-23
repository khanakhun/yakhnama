"""Unit tests for the audit outbox subscriber, with in-memory fakes only."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.audit import InMemoryAuditUnitOfWork, InMemoryEventTypeRegistry
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.audit.application.handlers import AuditSubscriber
from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.modules.provenance.domain.events import SourceRegistered
from yakhnama.modules.provenance.domain.value_objects import SourceType
from yakhnama.platform.outbox.envelope import OutboxEnvelope
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

if TYPE_CHECKING:
    # Only mypy needs it: the assignment below is the structural check.
    from yakhnama.platform.outbox.relay import Subscriber

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=401)
ACTOR_ID = IDS.new_id()


class _NoteRecorded(DomainEvent):
    """A test event that names its actor and request and carries free text.

    Implements: Domain Events.

    Attributes:
        actor_id: Who acted.
        request_id: The causing request.
        note: Free text that must never reach the audit log.
    """

    event_type: ClassVar[str] = "testing.note_recorded"

    actor_id: EntityId | None = None
    request_id: str | None = None
    note: str


def envelope(event: DomainEvent) -> OutboxEnvelope:
    """Wrap ``event`` exactly as the outbox writer and relay do."""
    return OutboxEnvelope(
        event_id=event.event_id,
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        occurred_at=event.occurred_at,
        payload=event.model_dump(mode="json"),
    )


def note(**fields: object) -> _NoteRecorded:
    """Return a note event with fresh ids."""
    return _NoteRecorded.model_validate(
        {
            "event_id": IDS.new_id(),
            "occurred_at": NOW,
            "aggregate_id": IDS.new_id(),
            "aggregate_type": "note",
            "note": "Sensitive words from a report description",
            **fields,
        }
    )


def subscriber() -> tuple[InMemoryAuditUnitOfWork, AuditSubscriber]:
    """Return a fake unit of work and a subscriber resolving the test events."""
    uow = InMemoryAuditUnitOfWork()
    registry = InMemoryEventTypeRegistry([_NoteRecorded, SourceRegistered])
    return uow, AuditSubscriber(
        InMemoryUnitOfWorkFactory(uow), registry, SequentialIdGenerator(seed=402)
    )


def test_audit_subscriber_satisfies_relay_subscriber_type() -> None:
    _, audit = subscriber()

    registered: Subscriber = audit

    assert callable(registered)


async def test_audit_subscriber_event_with_actor_appends_user_entry() -> None:
    uow, audit = subscriber()
    event = note(actor_id=str(ACTOR_ID), request_id="req-42")

    await audit(envelope(event))

    [entry] = uow.audit_entries.committed
    assert entry.event_id == event.event_id
    assert entry.actor_kind == "user"
    assert entry.actor_id == ACTOR_ID
    assert entry.request_id == "req-42"
    assert entry.action == "testing.note_recorded"
    assert entry.target == AuditTarget(target_type="note", target_id=event.aggregate_id)
    assert entry.occurred_at == NOW


async def test_audit_subscriber_entry_never_contains_free_text() -> None:
    uow, audit = subscriber()
    event = note()

    await audit(envelope(event))

    [entry] = uow.audit_entries.committed
    assert "Sensitive" not in entry.model_dump_json()
    assert entry.payload_digest.startswith("sha256:")


async def test_audit_subscriber_event_without_actor_appends_system_entry() -> None:
    uow, audit = subscriber()
    event = SourceRegistered(
        event_id=IDS.new_id(),
        occurred_at=NOW,
        aggregate_id=IDS.new_id(),
        version=1,
        source_type=SourceType.CITIZEN,
        owner_actor_id=ACTOR_ID,
        organization_id=None,
    )

    await audit(envelope(event))

    [entry] = uow.audit_entries.committed
    assert entry.actor_kind == "system"
    assert entry.actor_id is None
    assert entry.request_id is None


async def test_audit_subscriber_redelivered_event_appends_one_entry() -> None:
    uow, audit = subscriber()
    delivered = envelope(note())

    await audit(delivered)
    await audit(delivered)

    assert len(uow.audit_entries.committed) == 1
    assert uow.commit_count == 2
    assert uow.committed_events == ()


async def test_audit_subscriber_unknown_event_type_raises_validation_error() -> None:
    uow = InMemoryAuditUnitOfWork()
    audit = AuditSubscriber(
        InMemoryUnitOfWorkFactory(uow),
        InMemoryEventTypeRegistry(),
        SequentialIdGenerator(seed=403),
    )

    with pytest.raises(ValidationError):
        await audit(envelope(note()))

    assert uow.audit_entries.committed == []


async def test_audit_subscriber_malformed_actor_id_raises_and_appends_nothing() -> None:
    uow, audit = subscriber()
    delivered = envelope(note())
    broken = delivered.model_copy(
        update={"payload": {**delivered.payload, "actor_id": "not-a-uuid"}}
    )

    with pytest.raises(PydanticValidationError):
        await audit(broken)

    assert uow.audit_entries.committed == []
