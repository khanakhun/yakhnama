"""Deterministic ``IdGenerator`` fakes.

``SequentialIdGenerator`` produces *real* RFC 9562 UUIDv7 identifiers through the
kernel's own ``Uuid7Generator``, driven by a ``SteppingClock`` and a hash-based random
source instead of the operating system. The ids therefore pass every ``EntityId``
check, sort in creation order, embed predictable timestamps and are identical on every
run, which keeps snapshots and assertions stable. ``FixedIdGenerator`` hands out a
preset list for tests that need to know an id before the code under test creates it.

Patterns: Fake.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tests.fakes.clock import SteppingClock
from yakhnama.shared_kernel.ids import Uuid7Generator, is_uuid7

DEFAULT_ID_START = datetime(2026, 1, 1, tzinfo=UTC)
"""Instant embedded in the first id of a default ``SequentialIdGenerator``."""

DEFAULT_ID_STEP = timedelta(milliseconds=1)
"""Default gap between the timestamps of consecutive ids."""


class _HashRandomSource:
    """Deterministic byte source: SHA-256 of ``seed`` and a call counter.

    A hash rather than ``random.Random`` because the bytes only need to be stable and
    well spread, and the standard pseudo-random generator is flagged for any use near
    identifiers.

    Implements: Fake.
    """

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._calls = 0

    def __call__(self, count: int) -> bytes:
        digest = hashlib.sha256(f"{self._seed}:{self._calls}".encode()).digest()
        self._calls += 1
        # SHA-256 yields 32 bytes and the kernel asks for 10 per id; repeat the digest
        # anyway so the source honours any requested length.
        return (digest * (count // len(digest) + 1))[:count]


class SequentialIdGenerator:
    """``IdGenerator`` yielding stable, strictly increasing, valid UUIDv7 values.

    The n-th id (from 0) embeds ``start + n * step`` as its timestamp, truncated to
    the millisecond. Two generators built with the same arguments return the same
    sequence.

    Implements: Fake.

    Attributes:
        issued: Every id returned so far, in order.
    """

    def __init__(
        self,
        start: datetime = DEFAULT_ID_START,
        step: timedelta = DEFAULT_ID_STEP,
        seed: int = 0,
    ) -> None:
        """Create the generator.

        Args:
            start: Timestamp of the first id; must be timezone-aware.
            step: Timestamp gap between consecutive ids; strictly positive.
            seed: Selects the random bits, so two generators with different seeds
                yield different ids for the same timestamps.

        Raises:
            ValueError: If ``start`` is naive or ``step`` is not positive.
        """
        self._generator = Uuid7Generator(
            SteppingClock(start, step), random_source=_HashRandomSource(seed)
        )
        self.issued: list[UUID] = []

    def new_id(self) -> UUID:
        """Return the next id of the sequence.

        Returns:
            A UUIDv7 greater than every id this generator returned before.
        """
        identifier = self._generator.new_id()
        self.issued.append(identifier)
        return identifier


class FixedIdGenerator:
    """``IdGenerator`` that yields a preset list of UUIDv7 values, then fails.

    Implements: Fake.
    """

    def __init__(self, ids: Sequence[UUID]) -> None:
        """Create the generator.

        Args:
            ids: The ids to return, in order. Each must be a UUIDv7, because the
                ``IdGenerator`` contract promises one and ``EntityId`` fields reject
                anything else.

        Raises:
            ValueError: If any id is not a UUIDv7.
        """
        invalid = [str(identifier) for identifier in ids if not is_uuid7(identifier)]
        if invalid:
            message = f"FixedIdGenerator needs UUIDv7 values; got {invalid}"
            raise ValueError(message)
        self._ids = tuple(ids)
        self._position = 0

    @property
    def remaining(self) -> int:
        """Return how many preset ids have not been handed out yet."""
        return len(self._ids) - self._position

    def new_id(self) -> UUID:
        """Return the next preset id.

        Returns:
            The next id in the order given at construction.

        Raises:
            RuntimeError: If every preset id has been returned. Deliberately not a
                ``YakhnamaError``, so the code under test can never catch it and
                turn a broken test into a passing one.
        """
        if self._position >= len(self._ids):
            message = f"all {len(self._ids)} preset ids have been used"
            raise RuntimeError(message)
        identifier = self._ids[self._position]
        self._position += 1
        return identifier
