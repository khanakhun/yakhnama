"""Ports the audit application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1).

``EventEnvelope`` describes, structurally, what the outbox relay hands a subscriber
(``yakhnama.platform.outbox.envelope.OutboxEnvelope``). The application layer may not
import ``platform``, so it names only the members it uses; ``OutboxEnvelope``
satisfies the protocol as it is, and ``AuditSubscriber`` can be registered with the
relay's ``SubscriberRegistry`` directly.

Patterns: Repository (port side), Unit of Work, Query Service, Registry, Observer.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from yakhnama.modules.audit.application.dto import AuditEntrySummary
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.value_objects import AuditTarget
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class AuditEntryRepository(Protocol):
    """Appends audit entries; there is deliberately no get, save or delete.

    The log is append-only (``AGENTS.md`` §1); an adapter that needs to refuse a
    change or removal raises ``AuditEntryImmutableError``.

    Implements: Repository (port side).
    """

    async def append(self, entry: AuditEntry) -> bool:
        """Stage ``entry`` unless an entry for the same event already exists.

        Implementations make the check and the insert one statement (for example
        ``INSERT ... ON CONFLICT (event_id) DO NOTHING``), so two relays delivering
        the same event concurrently store it once.

        Args:
            entry: The new entry.

        Returns:
            ``True`` if the entry was staged, ``False`` if an entry for
            ``entry.event_id`` exists and nothing was staged.

        Raises:
            ConflictError: If the entry's own id is taken.
        """
        ...


class AuditUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the audit repository.

    Implements: Unit of Work.
    """

    @property
    def audit_entries(self) -> AuditEntryRepository:
        """Return the audit entry repository bound to this transaction."""
        ...


type AuditUnitOfWorkFactory = UnitOfWorkFactory[AuditUnitOfWork]
"""Opens a fresh audit unit of work per recorded event."""


class AuditQueryService(Protocol):
    """Read port for the audit log.

    Authorisation is applied by ``AuthorisedAuditQueryService`` before this port is
    called; implementations only read.

    Implements: Query Service.
    """

    async def list_for_target(
        self, target: AuditTarget, page: PageRequest
    ) -> Page[AuditEntrySummary]:
        """Return one page of the entries about ``target``.

        Ordered by ``occurred_at`` descending, then by entry id descending; the
        cursor's ``sort_key`` is ``occurred_at`` in ISO 8601 and its ``last_id`` the
        last entry's id.

        Args:
            target: The aggregate type and id.
            page: Page size and cursor.

        Returns:
            The page; empty if nothing about the target was recorded.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def list_recent(self, page: PageRequest) -> Page[AuditEntrySummary]:
        """Return one page of the whole log, in the order of ``list_for_target``.

        Args:
            page: Page size and cursor.

        Returns:
            The page.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...


class EventTypeRegistry(Protocol):
    """Maps an ``event_type`` to the domain event class that carries it.

    Supplied by the composition root, which knows every module's events; the audit
    module knows none of them.

    Implements: Registry.
    """

    def resolve(self, event_type: str) -> type[DomainEvent] | None:
        """Return the event class for ``event_type``.

        Args:
            event_type: A routing name such as ``reports.report_submitted``.

        Returns:
            The class, or ``None`` if no class is registered for it.
        """
        ...


class EventEnvelope(Protocol):
    """What the outbox relay hands a subscriber: one serialised domain event.

    Implements: Observer.
    """

    @property
    def event_id(self) -> UUID:
        """Return the event's id; subscribers de-duplicate by it."""
        ...

    @property
    def event_type(self) -> str:
        """Return the event's routing name."""
        ...

    @property
    def occurred_at(self) -> datetime:
        """Return when the change happened, UTC."""
        ...

    @property
    def payload(self) -> Mapping[str, object]:
        """Return the serialised event."""
        ...

    def to_event[EventT: DomainEvent](self, event_class: type[EventT]) -> EventT:
        """Rebuild the typed event.

        Args:
            event_class: The concrete class for this envelope's ``event_type``.

        Returns:
            The validated event.

        Raises:
            ValidationError: If ``event_class`` is for another ``event_type``.
            pydantic.ValidationError: If the payload does not fit ``event_class``.
        """
        ...
