"""A concrete domain event and deterministic id/clock sources for platform tests."""

from datetime import UTC, datetime, timedelta
from typing import ClassVar

from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import Uuid7Generator

FIXED_INSTANT = datetime(2026, 9, 23, 8, 30, tzinfo=UTC)


class FixedClock:
    """A ``Clock`` that returns a settable instant.

    Implements: Fake.

    Attributes:
        instant: The value ``now`` returns.
    """

    def __init__(self, instant: datetime = FIXED_INSTANT) -> None:
        """Create the clock at ``instant``."""
        self.instant = instant

    def now(self) -> datetime:
        """Return ``instant``."""
        return self.instant

    def advance(self, seconds: float) -> None:
        """Move ``instant`` forward by ``seconds``."""
        self.instant += timedelta(seconds=seconds)


class SampleRecorded(DomainEvent):
    """An event used only by the platform tests.

    Implements: Domain Events.

    Attributes:
        note: Arbitrary payload field.
    """

    event_type: ClassVar[str] = "testing.sample_recorded"

    note: str


class OtherRecorded(DomainEvent):
    """A second event type, for routing and mismatch tests.

    Implements: Domain Events.
    """

    event_type: ClassVar[str] = "testing.other_recorded"


def make_event(
    note: str = "first", *, id_generator: Uuid7Generator | None = None
) -> SampleRecorded:
    """Return a valid ``SampleRecorded`` with UUIDv7 ids."""
    generator = id_generator if id_generator is not None else Uuid7Generator()
    return SampleRecorded(
        event_id=generator.new_id(),
        occurred_at=FIXED_INSTANT,
        aggregate_id=generator.new_id(),
        aggregate_type="sample",
        note=note,
    )
