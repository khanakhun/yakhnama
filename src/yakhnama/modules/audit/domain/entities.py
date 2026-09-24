"""The append-only ``AuditEntry``.

An audit entry is written once, by the outbox subscriber, for every domain event,
and never changed or deleted (``AGENTS.md`` §1: auditability beats speed). The model
is frozen and has **no methods that change state**; there is nothing to evolve and
no event to emit (see ``events.py``). Anything that tries to change an entry is a
defect, reported with ``AuditEntryImmutableError``.

Patterns: Entity.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from yakhnama.modules.audit.domain.value_objects import (
    ActorKind,
    AuditAction,
    AuditTarget,
    Digest,
    RequestId,
)
from yakhnama.shared_kernel.ids import EntityId


class AuditEntry(BaseModel):
    """One immutable line of the audit log, about one domain event.

    Invariants, checked on construction:

    - ``actor_kind`` is ``user`` exactly when ``actor_id`` is set (**proposed**: a
      system actor has no id, a user always has one);
    - ``occurred_at`` is timezone-aware and normalised to UTC.

    Implements: Entity.

    Attributes:
        id: Stable identity of the entry (UUIDv7).
        occurred_at: When the recorded change happened (the event's
            ``occurred_at``), UTC.
        actor_id: The user who caused the change, or ``None`` for the system.
        actor_kind: ``user`` or ``system``.
        action: What happened, normally the event's ``event_type``.
        target: The aggregate the change was about.
        before_digest: Digest of the aggregate state before the change, if known.
        after_digest: Digest of the aggregate state after the change, if known.
        request_id: Correlation id of the causing request or task, if any.
        event_id: Id of the recorded domain event; unique among entries, so a
            redelivered event is recorded once.
        payload_digest: Digest of the event's canonical JSON.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    occurred_at: AwareDatetime
    actor_id: EntityId | None
    actor_kind: ActorKind
    action: AuditAction
    target: AuditTarget
    before_digest: Digest | None = None
    after_digest: Digest | None = None
    request_id: RequestId | None = None
    event_id: EntityId
    payload_digest: Digest

    @field_validator("occurred_at", mode="after")
    @classmethod
    def _normalise_occurred_at(cls, occurred_at: datetime) -> datetime:
        return occurred_at.astimezone(UTC)

    @model_validator(mode="after")
    def _check_actor(self) -> Self:
        if (self.actor_kind == "user") != (self.actor_id is not None):
            message = "actor_id must be set exactly when actor_kind is 'user'"
            raise ValueError(message)
        return self
