"""Read models returned by the audit query service.

An audit entry holds only ids, codes and digests, so its DTO can show every field
to the administrators who may read the log.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import (
    ActorKind,
    AuditAction,
    AuditTarget,
    Digest,
    RequestId,
)
from yakhnama.shared_kernel.ids import EntityId


class AuditEntrySummary(BaseModel):
    """One line of the audit log.

    Implements: DTO.

    Attributes:
        id: The entry's id.
        occurred_at: When the recorded change happened, UTC.
        actor_id: The user who caused it, or ``None`` for the system.
        actor_kind: ``user`` or ``system``.
        action: What happened (the event type).
        target: The aggregate it was about.
        before_digest: Digest of the state before the change, if known.
        after_digest: Digest of the state after the change, if known.
        request_id: Correlation id of the causing request or task, if any.
        event_id: The recorded domain event.
        payload_digest: Digest of the event's canonical JSON.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    occurred_at: AwareDatetime
    actor_id: EntityId | None
    actor_kind: ActorKind
    action: AuditAction
    target: AuditTarget
    before_digest: Digest | None
    after_digest: Digest | None
    request_id: RequestId | None
    event_id: EntityId
    payload_digest: Digest

    @classmethod
    def from_entity(cls, entry: AuditEntry) -> Self:
        """Build the summary of an entry.

        Args:
            entry: The entry.

        Returns:
            Its summary, field for field.
        """
        return cls.model_validate(entry.model_dump())
