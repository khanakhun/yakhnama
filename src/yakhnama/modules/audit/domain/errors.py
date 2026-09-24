"""Errors of the ``audit`` bounded context.

The audit log is append-only: there is no update or delete path anywhere. These
errors exist for the application and persistence layers, which raise
``AuditEntryImmutableError`` from any code path that would change or remove an entry
(for example a repository method or a database trigger translated by an adapter).

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import Self

from yakhnama.shared_kernel.errors import InvariantViolationError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId


class AuditEntryNotFoundError(NotFoundError):
    """No audit entry exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, entry_id: EntityId) -> Self:
        """Build the error for a missing entry id.

        Args:
            entry_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such audit entry", details={"audit_entry_id": str(entry_id)})


class AuditEntryImmutableError(InvariantViolationError):
    """Something attempted to change or delete an audit entry.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, entry_id: EntityId) -> Self:
        """Build the error for a change attempted on an entry.

        Args:
            entry_id: Id of the entry.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "audit entries are append-only and never change",
            details={"audit_entry_id": str(entry_id)},
        )
