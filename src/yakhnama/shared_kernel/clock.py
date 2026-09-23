"""The ``Clock`` port and its system adapter.

Time is injected rather than read with ``datetime.now`` wherever it is needed, so tests
can freeze or advance it and every timestamp in the record is reproducible
(``AGENTS.md`` §4.2). The port also concentrates the "always timezone-aware, always
UTC" rule in one place instead of trusting every call site (``AGENTS.md`` §4, hard
rules).

Patterns: Adapter (the ``Clock`` port and ``SystemClock``).
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current instant.

    Implements: Adapter (port side).
    """

    def now(self) -> datetime:
        """Return the current instant.

        Returns:
            A timezone-aware ``datetime`` whose ``tzinfo`` is ``datetime.UTC``.
        """
        ...


class SystemClock:
    """``Clock`` backed by the operating system's real-time clock.

    Implements: Adapter.
    """

    def now(self) -> datetime:
        """Return the current instant from the operating system.

        Returns:
            The current time as a timezone-aware UTC ``datetime``.
        """
        return datetime.now(UTC)
