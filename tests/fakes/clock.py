"""Deterministic ``Clock`` fakes.

Both fakes honour the ``yakhnama.shared_kernel.clock.Clock`` contract: every instant
they return is timezone-aware and in ``datetime.UTC``. A naive start instant is
rejected when the fake is built, so a test can never feed naive time into the domain
by accident (``AGENTS.md`` §4, hard rules). A test that deliberately needs a
misbehaving clock (for example one returning naive datetimes) keeps its own private
double, because these fakes refuse to misbehave.

Patterns: Fake.
"""

from datetime import UTC, datetime, timedelta


def _require_aware(instant: datetime, name: str) -> datetime:
    """Return ``instant`` in UTC, rejecting a naive ``datetime``.

    Args:
        instant: The instant to check.
        name: Parameter name used in the error message.

    Returns:
        ``instant`` converted to ``datetime.UTC``.

    Raises:
        ValueError: If ``instant`` has no UTC offset.
    """
    # ValueError rather than a Yakhnama error: this is a mistake in the test itself,
    # and a domain error could be caught and mapped by the code under test.
    if instant.utcoffset() is None:
        message = f"{name} must be timezone-aware; naive datetimes are not allowed"
        raise ValueError(message)
    return instant.astimezone(UTC)


class FrozenClock:
    """``Clock`` that returns the same instant until the test moves it.

    Implements: Fake.

    Attributes:
        calls: How many times ``now`` has been called.
    """

    def __init__(self, now: datetime) -> None:
        """Freeze the clock at ``now``.

        Args:
            now: The instant every call returns; converted to UTC.

        Raises:
            ValueError: If ``now`` is naive.
        """
        self._now = _require_aware(now, "now")
        self.calls = 0

    def now(self) -> datetime:
        """Return the frozen instant.

        Returns:
            The current frozen instant, timezone-aware in UTC.
        """
        self.calls += 1
        return self._now

    def move_to(self, instant: datetime) -> None:
        """Set the frozen instant, forwards or backwards.

        Args:
            instant: The new instant; converted to UTC.

        Raises:
            ValueError: If ``instant`` is naive.
        """
        self._now = _require_aware(instant, "instant")

    def advance(self, delta: timedelta) -> None:
        """Move the frozen instant by ``delta``; a negative delta moves it back.

        Args:
            delta: How far to move the clock.
        """
        self._now += delta


class SteppingClock:
    """``Clock`` that returns ``start`` first and then advances ``step`` per call.

    Useful where a test needs every timestamp to differ, for example to check that
    ``updated_at`` moves forward on each change.

    Implements: Fake.

    Attributes:
        calls: How many times ``now`` has been called.
    """

    def __init__(self, start: datetime, step: timedelta) -> None:
        """Create the clock.

        Args:
            start: The first instant returned; converted to UTC.
            step: How far the clock advances after every call; strictly positive,
                because a zero step is a ``FrozenClock`` and a negative one breaks
                the "time moves forward" expectation this fake exists for.

        Raises:
            ValueError: If ``start`` is naive or ``step`` is not positive.
        """
        if step <= timedelta(0):
            message = "step must be positive; use FrozenClock for a clock that stops"
            raise ValueError(message)
        self._next = _require_aware(start, "start")
        self._step = step
        self.calls = 0

    def now(self) -> datetime:
        """Return the next instant and advance the clock by one step.

        Returns:
            ``start + calls * step`` before this call, timezone-aware in UTC.
        """
        current = self._next
        self._next = current + self._step
        self.calls += 1
        return current

    def peek(self) -> datetime:
        """Return the instant the next ``now`` call will return, without advancing.

        Returns:
            The next instant, timezone-aware in UTC.
        """
        return self._next
