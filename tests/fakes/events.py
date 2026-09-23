"""An ``EventRecorder`` fake that keeps every event it is given.

Patterns: Fake.
"""

from yakhnama.shared_kernel.events import DomainEvent


class RecordingEventRecorder:
    """``EventRecorder`` that appends every recorded event to ``events``.

    Implements: Fake.

    Attributes:
        events: The recorded events, in the order they were recorded.
    """

    def __init__(self) -> None:
        """Create an empty recorder."""
        self.events: list[DomainEvent] = []

    def record_event(self, event: DomainEvent) -> None:
        """Keep ``event``.

        Args:
            event: The event to record.
        """
        self.events.append(event)

    def events_of_type[EventT: DomainEvent](
        self, event_class: type[EventT]
    ) -> tuple[EventT, ...]:
        """Return the recorded events that are instances of ``event_class``.

        Args:
            event_class: The event class to filter by, subclasses included.

        Returns:
            The matching events, in recording order.
        """
        return tuple(event for event in self.events if isinstance(event, event_class))
