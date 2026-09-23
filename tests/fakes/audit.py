"""In-memory fakes of the audit ports.

``InMemoryAuditEntryRepository`` is append-only like the SQL adapter: it has no way
to change or remove an entry, and ``append`` skips an entry whose ``event_id`` is
already stored or staged. Tests hand the subscriber the platform's real
``OutboxEnvelope``, so no envelope fake is needed.

Patterns: Fake.
"""

from collections.abc import Iterable
from datetime import datetime

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.audit.application.dto import AuditEntrySummary
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)


class InMemoryAuditEntryRepository:
    """``AuditEntryRepository`` over a list, append only.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored entries in append order.
    """

    def __init__(self, entries: Iterable[AuditEntry] = ()) -> None:
        """Create the repository.

        Args:
            entries: Entries that exist before the test acts.
        """
        self.committed: list[AuditEntry] = list(entries)
        self._staged: list[AuditEntry] = []

    async def append(self, entry: AuditEntry) -> bool:
        """Stage ``entry`` unless its event is already recorded.

        Args:
            entry: The new entry.

        Returns:
            ``True`` if staged, ``False`` if the event was already recorded.

        Raises:
            ConflictError: If the entry id is taken.
        """
        current = [*self.committed, *self._staged]
        if any(stored.event_id == entry.event_id for stored in current):
            return False
        if any(stored.id == entry.id for stored in current):
            message = "the audit entry id is taken"
            raise ConflictError(message)
        self._staged.append(entry)
        return True

    def apply_staged(self) -> None:
        """Make the staged entries permanent; called on commit."""
        self.committed.extend(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged entries; called on rollback."""
        self._staged.clear()


class InMemoryAuditUnitOfWork(InMemoryUnitOfWork):
    """``AuditUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        audit_entries: The entry repository bound to this unit of work.
    """

    def __init__(self, *, entries: Iterable[AuditEntry] = ()) -> None:
        """Create the unit of work.

        Args:
            entries: Entries that exist before the test acts.
        """
        super().__init__()
        self.audit_entries = InMemoryAuditEntryRepository(entries)

    def _on_commit(self) -> None:
        self.audit_entries.apply_staged()

    def _on_rollback(self) -> None:
        self.audit_entries.discard_staged()


def _newest_first(entry: AuditEntry) -> tuple[datetime, EntityId]:
    return entry.occurred_at, entry.id


class InMemoryAuditQueryService:
    """``AuditQueryService`` reading a fake unit of work's committed entries.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryAuditUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed entries are served.
        """
        self._uow = uow

    async def list_for_target(
        self, target: AuditTarget, page: PageRequest
    ) -> Page[AuditEntrySummary]:
        """Page the entries about ``target``, newest first.

        Args:
            target: The aggregate.
            page: Page size and cursor.

        Returns:
            One page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        entries = [
            entry
            for entry in self._uow.audit_entries.committed
            if entry.target == target
        ]
        return self._page(entries, page)

    async def list_recent(self, page: PageRequest) -> Page[AuditEntrySummary]:
        """Page every entry, newest first.

        Args:
            page: Page size and cursor.

        Returns:
            One page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        return self._page(list(self._uow.audit_entries.committed), page)

    @staticmethod
    def _page(entries: list[AuditEntry], page: PageRequest) -> Page[AuditEntrySummary]:
        cursor = page.decode_cursor()
        ordered = sorted(entries, key=_newest_first, reverse=True)
        if cursor is not None:
            before = (datetime.fromisoformat(cursor.sort_key), cursor.last_id)
            ordered = [entry for entry in ordered if _newest_first(entry) < before]
        window = ordered[: page.limit]
        next_cursor = None
        if len(ordered) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.occurred_at.isoformat(), last_id=last.id)
            )
        return Page[AuditEntrySummary](
            items=tuple(AuditEntrySummary.from_entity(entry) for entry in window),
            next_cursor=next_cursor,
        )


class InMemoryEventTypeRegistry:
    """``EventTypeRegistry`` over a dictionary.

    Implements: Fake (of Registry).
    """

    def __init__(self, event_classes: Iterable[type[DomainEvent]] = ()) -> None:
        """Create the registry.

        Args:
            event_classes: The classes to resolve, keyed by their ``event_type``.
        """
        self._classes = {
            event_class.event_type: event_class for event_class in event_classes
        }

    def resolve(self, event_type: str) -> type[DomainEvent] | None:
        """Return the class registered for ``event_type``.

        Args:
            event_type: The routing name.

        Returns:
            The class, or ``None``.
        """
        return self._classes.get(event_type)
