"""Translate between the ``AuditEntry`` entity and its row model.

Every read goes through ``model_validate``, so a row that no longer satisfies the
domain's rules (a malformed digest, a user entry without an actor id) fails loudly.
``recorded_at`` is the database's bookkeeping and has no domain counterpart.

Patterns: Anti-Corruption Layer (mapper).
"""

from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.infrastructure.orm import AuditEntryRow


def entry_to_values(entry: AuditEntry) -> dict[str, object]:
    """Return the column values of ``entry`` for an insert.

    ``recorded_at`` is left out so the database's ``now()`` default sets it.

    Args:
        entry: The entry.

    Returns:
        Column name to value.
    """
    return {
        "id": entry.id,
        "event_id": entry.event_id,
        "occurred_at": entry.occurred_at,
        "actor_id": entry.actor_id,
        "actor_kind": entry.actor_kind,
        "action": entry.action,
        "target_type": entry.target.target_type,
        "target_id": entry.target.target_id,
        "before_digest": entry.before_digest,
        "after_digest": entry.after_digest,
        "payload_digest": entry.payload_digest,
        "request_id": entry.request_id,
    }


def row_to_entry(row: AuditEntryRow) -> AuditEntry:
    """Rebuild an entry from its row.

    Args:
        row: A row loaded from ``audit_entries``.

    Returns:
        The validated ``AuditEntry``.
    """
    return AuditEntry.model_validate(
        {
            "id": row.id,
            "event_id": row.event_id,
            "occurred_at": row.occurred_at,
            "actor_id": row.actor_id,
            "actor_kind": row.actor_kind,
            "action": row.action,
            "target": {"target_type": row.target_type, "target_id": row.target_id},
            "before_digest": row.before_digest,
            "after_digest": row.after_digest,
            "payload_digest": row.payload_digest,
            "request_id": row.request_id,
        }
    )
