"""The outbox subscriber that writes the audit log.

``AuditSubscriber`` is registered with the outbox relay for every event type the
composition root wants audited. For each delivered envelope it rebuilds the typed
domain event, turns it into an ``AuditEntry`` with ``from_domain_event`` (ids, codes
and a payload digest only; no free text is copied) and appends it in a unit of work
of its own.

Delivery is at least once (ADR 0007), so the subscriber is idempotent by
``event_id``: the repository's ``append`` stores nothing when an entry for the event
exists, and the subscriber then commits an empty transaction. It records no domain
events of its own (``audit.domain.events``).

The acting user and the request id come from the event payload's ``actor_id`` and
``request_id`` keys when the event carries them; otherwise the entry records the
``system`` actor and no request id. A malformed value in either key fails the
delivery (the relay records it and retries) instead of being dropped silently.

There is no policy check: the subscriber is not a use case any actor invokes. It is
reachable only from the relay, and it writes what already happened.

Patterns: Observer, Unit of Work, Registry.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.audit.application.ports import (
    AuditUnitOfWorkFactory,
    EventEnvelope,
    EventTypeRegistry,
)
from yakhnama.modules.audit.domain.factories import from_domain_event
from yakhnama.modules.audit.domain.value_objects import RequestId
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator


class _PayloadAttribution(BaseModel):
    """The optional attribution keys of an event payload.

    Implements: DTO.

    Attributes:
        actor_id: The acting user, if the event names one.
        request_id: The causing request or task, if the event names one.
    """

    # Every other key of the payload is the event's own business.
    model_config = ConfigDict(frozen=True, extra="ignore")

    actor_id: EntityId | None = None
    request_id: RequestId | None = None


class AuditSubscriber:
    """Record every delivered domain event as one audit entry.

    Implements: Observer.
    """

    def __init__(
        self,
        uow_factory: AuditUnitOfWorkFactory,
        event_types: EventTypeRegistry,
        ids: IdGenerator,
    ) -> None:
        """Create the subscriber.

        Args:
            uow_factory: Opens an audit unit of work per envelope.
            event_types: Resolves each envelope's event class.
            ids: Source of entry ids.
        """
        self._uow_factory = uow_factory
        self._event_types = event_types
        self._ids = ids

    async def __call__(self, envelope: EventEnvelope) -> None:
        """Append the audit entry for ``envelope``; a redelivery is a no-op.

        Args:
            envelope: The delivered event.

        Raises:
            ValidationError: If no event class is registered for the envelope's
                ``event_type``.
            pydantic.ValidationError: If the payload does not fit the event class,
                or its ``actor_id`` or ``request_id`` is malformed.
        """
        event_class = self._event_types.resolve(envelope.event_type)
        if event_class is None:
            message = "no event class is registered for this event type"
            raise ValidationError(message, details={"event_type": envelope.event_type})
        event = envelope.to_event(event_class)
        attribution = _PayloadAttribution.model_validate(dict(envelope.payload))
        entry = from_domain_event(
            event,
            actor_id=attribution.actor_id,
            request_id=attribution.request_id,
            ids=self._ids,
        )
        async with self._uow_factory() as uow:
            await uow.audit_entries.append(entry)
            await uow.commit()
