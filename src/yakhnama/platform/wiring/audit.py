"""The audit module's event-type registry and its subscription to every event.

``AuditSubscriber`` must rebuild each delivered envelope into its typed domain event,
so it depends on an ``EventTypeRegistry`` port; the audit module knows no other
module's events. ``DomainEventTypeRegistry`` implements that port from the concrete
event classes of every module, and ``subscribe_to_every_event`` registers one
subscriber for each of their ``event_type`` values on the outbox relay.

Internal imports: the event classes are collected from each module's
``domain/events.py`` (``EVENT_MODULES``). No facade exports a module's full event
list, and a hand-written list here would silently miss an event added later; the
composition root is the one place allowed to reach into every module. A
``public.EVENT_TYPES`` export per module is an open question for the module owners.
A module whose ``domain/events.py`` defines no events (audit records none of its own)
contributes nothing.

Patterns: Registry, Observer.
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType, ModuleType
from typing import Final

from yakhnama.modules.events.domain import events as events_events
from yakhnama.modules.geography.domain import events as geography_events
from yakhnama.modules.hazards.domain import events as hazards_events
from yakhnama.modules.identity.domain import events as identity_events
from yakhnama.modules.impacts.domain import events as impacts_events
from yakhnama.modules.media.domain import events as media_events
from yakhnama.modules.provenance.domain import events as provenance_events
from yakhnama.modules.reports.domain import events as reports_events
from yakhnama.modules.verification.domain import events as verification_events
from yakhnama.platform.outbox.relay import Subscriber, SubscriberRegistry
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.events import DomainEvent

EVENT_MODULES: Final[tuple[ModuleType, ...]] = (
    geography_events,
    hazards_events,
    impacts_events,
    identity_events,
    provenance_events,
    reports_events,
    media_events,
    events_events,
    verification_events,
)
"""Every module's ``domain/events.py`` that defines domain events."""


def concrete_event_classes(module: ModuleType) -> tuple[type[DomainEvent], ...]:
    """Return the publishable event classes defined in ``module``.

    A class counts when it is a ``DomainEvent`` defined in ``module`` itself (not
    imported into it) and declares its own ``event_type``; shared bases such as
    ``ReportEvent`` declare none and are left out.

    Args:
        module: A module's ``domain/events.py``.

    Returns:
        The classes, in definition order.
    """
    return tuple(
        value
        for value in vars(module).values()
        if isinstance(value, type)
        and issubclass(value, DomainEvent)
        and value.__module__ == module.__name__
        and "event_type" in value.__dict__
    )


class DomainEventTypeRegistry:
    """``EventTypeRegistry`` over a fixed set of domain event classes.

    Implements: Registry.
    """

    def __init__(self, event_classes: Iterable[type[DomainEvent]]) -> None:
        """Index the classes by ``event_type``.

        Args:
            event_classes: Concrete domain event classes.

        Raises:
            ConflictError: If two classes declare the same ``event_type``; the
                outbox could not tell their envelopes apart.
        """
        by_type: dict[str, type[DomainEvent]] = {}
        for event_class in event_classes:
            known = by_type.setdefault(event_class.event_type, event_class)
            if known is not event_class:
                message = f"two event classes declare {event_class.event_type}"
                raise ConflictError(
                    message, details={"event_type": event_class.event_type}
                )
        self._by_type: Mapping[str, type[DomainEvent]] = MappingProxyType(by_type)

    @classmethod
    def from_modules(cls, modules: Iterable[ModuleType]) -> "DomainEventTypeRegistry":
        """Build the registry from modules' ``domain/events.py``.

        Args:
            modules: The event modules, for example ``EVENT_MODULES``.

        Returns:
            The registry of every concrete event class they define.

        Raises:
            ConflictError: If two classes declare the same ``event_type``.
        """
        return cls(
            event_class
            for module in modules
            for event_class in concrete_event_classes(module)
        )

    def resolve(self, event_type: str) -> type[DomainEvent] | None:
        """Return the event class for ``event_type``.

        Args:
            event_type: A routing name such as ``reports.report_submitted``.

        Returns:
            The class, or ``None`` if no class is registered for it.
        """
        return self._by_type.get(event_type)

    def event_types(self) -> tuple[str, ...]:
        """Return every registered ``event_type``, sorted.

        Returns:
            The routing names.
        """
        return tuple(sorted(self._by_type))


def subscribe_to_every_event(
    registry: SubscriberRegistry,
    event_types: DomainEventTypeRegistry,
    subscriber: Subscriber,
) -> None:
    """Register ``subscriber`` on the relay for every known ``event_type``.

    Args:
        registry: The outbox relay's subscriber registry.
        event_types: The event types to subscribe to.
        subscriber: The subscriber, for example ``AuditSubscriber``.

    Raises:
        ConflictError: If the subscriber is already registered for one of them.
    """
    for event_type in event_types.event_types():
        registry.subscribe(event_type, subscriber)
