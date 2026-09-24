"""Public facade of the ``audit`` module.

The composition root imports the subscriber, the ports it binds and the event-type
registry port it implements; the API imports the queries, the DTO and the
authorised query service.

Patterns: Facade.
"""

from yakhnama.modules.audit.application.dto import AuditEntrySummary
from yakhnama.modules.audit.application.handlers import AuditSubscriber
from yakhnama.modules.audit.application.ports import (
    AuditEntryRepository,
    AuditQueryService,
    AuditUnitOfWork,
    AuditUnitOfWorkFactory,
    EventEnvelope,
    EventTypeRegistry,
)
from yakhnama.modules.audit.application.queries import (
    ListAuditEntriesForTarget,
    ListRecentAuditEntries,
)
from yakhnama.modules.audit.application.query_services import (
    AuthorisedAuditQueryService,
)
from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.domain.errors import (
    AuditEntryImmutableError,
    AuditEntryNotFoundError,
)
from yakhnama.modules.audit.domain.value_objects import AuditTarget

__all__ = [
    "AuditEntry",
    "AuditEntryImmutableError",
    "AuditEntryNotFoundError",
    "AuditEntryRepository",
    "AuditEntrySummary",
    "AuditQueryService",
    "AuditSubscriber",
    "AuditTarget",
    "AuditUnitOfWork",
    "AuditUnitOfWorkFactory",
    "AuthorisedAuditQueryService",
    "EventEnvelope",
    "EventTypeRegistry",
    "ListAuditEntriesForTarget",
    "ListRecentAuditEntries",
]
