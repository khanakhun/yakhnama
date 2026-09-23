"""Domain events and the minimal machinery aggregates need to emit them.

A domain event records something that has happened to one aggregate. Class names are
past-tense nouns (``ReportSubmitted``, ``HazardTypeRetired``) and every concrete event
declares a stable ``event_type`` of the form ``<bounded_context>.<snake_case_name>``,
for example ``"hazards.hazard_type_retired"``; the outbox stores and routes events by
this string, so it never changes once published.

Aggregates are immutable, so a state-changing method cannot append to an internal
event list. It returns an ``AggregateChange`` instead: the new state plus the events
the change produced. The handler hands both to the unit of work
(``change.record_into(uow)``), which writes the events to the outbox in the same
transaction (ADR 0007). ``event_id`` comes from the injected ``IdGenerator`` and
``occurred_at`` from the injected ``Clock``, both passed into the aggregate method.

Patterns: Domain Events.
"""

import re
from datetime import UTC, datetime
from typing import ClassVar, Protocol, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.shared_kernel.ids import EntityId

EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"
EVENT_TYPE_MAX_LENGTH = 100
_EVENT_TYPE_REGEX = re.compile(EVENT_TYPE_PATTERN)


class DomainEvent(BaseModel):
    """Base class for every domain event.

    Subclasses add the event's own fields and set ``event_type``; instantiating a class
    without an ``event_type`` raises ``TypeError``, so the base cannot be published by
    mistake.

    Implements: Domain Events.

    Attributes:
        event_type: Stable routing name, ``<bounded_context>.<snake_case_name>``.
        event_id: Identifier of this event occurrence (UUIDv7).
        occurred_at: When the change happened, normalised to UTC.
        aggregate_id: Identifier of the aggregate that changed (UUIDv7).
        aggregate_type: Snake-case name of the aggregate type, for example
            ``"hazard_type"``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: ClassVar[str]

    event_id: EntityId
    occurred_at: AwareDatetime
    aggregate_id: EntityId
    aggregate_type: str = Field(
        min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$"
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """Reject a malformed ``event_type`` when the subclass is defined.

        Args:
            **kwargs: Class keywords forwarded by Pydantic.

        Raises:
            TypeError: If the subclass declares an ``event_type`` that does not match
                ``EVENT_TYPE_PATTERN``.
        """
        super().__pydantic_init_subclass__(**kwargs)
        event_type = cls.__dict__.get("event_type")
        if event_type is not None and not is_event_type(event_type):
            message = (
                f"{cls.__name__}.event_type {event_type!r} must match "
                f"{EVENT_TYPE_PATTERN}"
            )
            raise TypeError(message)

    @model_validator(mode="after")
    def _require_event_type(self) -> Self:
        # TypeError, not ValueError: a missing event_type is a programming mistake in
        # the class definition, not bad input, so Pydantic must not wrap it.
        if not hasattr(type(self), "event_type"):
            message = f"{type(self).__name__} does not declare an event_type"
            raise TypeError(message)
        return self

    @field_validator("occurred_at", mode="after")
    @classmethod
    def _normalise_occurred_at(cls, occurred_at: datetime) -> datetime:
        try:
            return occurred_at.astimezone(UTC)
        except OverflowError as error:
            message = "occurred_at is outside the representable UTC range"
            raise ValueError(message) from error


def is_event_type(value: object) -> bool:
    """Tell whether ``value`` is a well-formed ``event_type``.

    Args:
        value: A candidate such as ``"hazards.hazard_type_retired"``.

    Returns:
        ``True`` if it is a string of at most ``EVENT_TYPE_MAX_LENGTH`` characters
        matching ``EVENT_TYPE_PATTERN``.
    """
    return (
        isinstance(value, str)
        and len(value) <= EVENT_TYPE_MAX_LENGTH
        and _EVENT_TYPE_REGEX.fullmatch(value) is not None
    )


class EventRecorder(Protocol):
    """Anything that accepts domain events for publication, such as a unit of work.

    Implements: Domain Events (recorder port).
    """

    def record_event(self, event: DomainEvent) -> None:
        """Queue ``event`` for publication when the surrounding work commits.

        Args:
            event: The event to publish, in the order it happened.
        """
        ...


class AggregateChange[StateT: BaseModel](BaseModel):
    """The result of a state-changing aggregate method: new state plus its events.

    Implements: Domain Events.

    Attributes:
        state: The new aggregate instance.
        events: Events produced by the change, in the order they happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: StateT
    events: tuple[DomainEvent, ...] = Field(default=(), max_length=100)

    def record_into(self, recorder: EventRecorder) -> StateT:
        """Hand every event to ``recorder`` in order and return the new state.

        Args:
            recorder: Usually the handler's unit of work.

        Returns:
            ``state``, so a handler can save it in the same expression.
        """
        for event in self.events:
            recorder.record_event(event)
        return self.state
