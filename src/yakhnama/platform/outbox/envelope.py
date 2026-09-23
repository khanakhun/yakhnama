"""What a subscriber receives: an outbox row as an immutable, typed envelope.

Subscribers never see the ORM row, so they cannot change delivery bookkeeping, and
they rebuild their own typed event with ``OutboxEnvelope.to_event``.

Patterns: DTO (proposed in ADR 0012).
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue

from yakhnama.platform.outbox.models import OutboxMessage
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId


class OutboxEnvelope(BaseModel):
    """One outbox message as handed to subscribers.

    Implements: DTO (proposed in ADR 0012).

    Attributes:
        event_id: The event's identifier; subscribers de-duplicate by it.
        event_type: Routing name the subscriber was registered for.
        aggregate_type: Snake-case aggregate name.
        aggregate_id: Identifier of the aggregate that changed.
        occurred_at: When the change happened (UTC).
        payload: The complete serialised event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: EntityId
    event_type: str
    aggregate_type: str
    aggregate_id: EntityId
    occurred_at: AwareDatetime
    # JSON by nature: this is the serialised event crossing the outbox boundary; it
    # becomes a typed DomainEvent again through ``to_event``.
    payload: dict[str, JsonValue]

    @classmethod
    def from_message(cls, message: OutboxMessage) -> Self:
        """Build the envelope for one outbox row.

        Args:
            message: A row loaded by the relay.

        Returns:
            An immutable copy of the row's event data.
        """
        return cls(
            event_id=message.id,
            event_type=message.event_type,
            aggregate_type=message.aggregate_type,
            aggregate_id=message.aggregate_id,
            occurred_at=message.occurred_at,
            payload=message.payload,
        )

    def to_event[EventT: DomainEvent](self, event_class: type[EventT]) -> EventT:
        """Rebuild the typed domain event carried by this envelope.

        Args:
            event_class: The concrete event class, whose ``event_type`` must equal
                this envelope's.

        Returns:
            The validated event.

        Raises:
            ValidationError: If ``event_class`` is for another ``event_type``.
            pydantic.ValidationError: If the payload does not fit ``event_class``.
        """
        if event_class.event_type != self.event_type:
            message = (
                f"envelope carries {self.event_type}, "
                f"not {event_class.event_type} ({event_class.__name__})"
            )
            raise ValidationError(message)
        return event_class.model_validate(self.payload)
