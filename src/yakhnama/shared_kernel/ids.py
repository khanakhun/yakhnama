"""Identifier generation: the ``IdGenerator`` port and an in-house RFC 9562 UUIDv7.

Every identifier in Yakhnama is a UUIDv7 (ADR 0006). The generator is implemented here
rather than taken from a library or from Python 3.14's ``uuid.uuid7`` so that Python
3.13 and 3.14 share one tested code path with no new dependency (ADR 0013, proposed).

Bit layout produced by ``Uuid7Generator`` (RFC 9562 §5.7, monotonicity per §6.2,
"Method 1: fixed bit-length dedicated counter", with a 42-bit counter)::

    48 bits  unix_ts_ms       milliseconds since 1970-01-01T00:00:00Z
     4 bits  ver              0b0111
    12 bits  rand_a           high 12 bits of the counter
     2 bits  var              0b10
    30 bits  rand_b (high)    low 30 bits of the counter
    32 bits  rand_b (low)     fresh random bits for every identifier

Patterns: Adapter (the ``IdGenerator`` port and ``Uuid7Generator``).
"""

import secrets
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import AfterValidator

from yakhnama.shared_kernel.clock import Clock, SystemClock
from yakhnama.shared_kernel.errors import InvariantViolationError, ValidationError

UUID_VERSION_7 = 7
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MILLISECOND = timedelta(milliseconds=1)

_COUNTER_BITS = 42
_COUNTER_LOW_BITS = 30
_TAIL_BITS = 32
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1
# RFC 9562 §6.2 "counter rollover guards": seeding with the most significant counter
# bit cleared leaves at least 2**41 increments before the counter can roll over.
_COUNTER_SEED_MASK = (1 << (_COUNTER_BITS - 1)) - 1
_COUNTER_SEED_BYTES = 6
_TAIL_BYTES = _TAIL_BITS // 8
RANDOM_BYTES_PER_ID = _COUNTER_SEED_BYTES + _TAIL_BYTES

_VERSION_FIELD = 0x7 << 76
_VARIANT_FIELD = 0b10 << 62

RandomSource = Callable[[int], bytes]


class IdGenerator(Protocol):
    """Source of new identifiers for entities, events and outbox messages.

    Implements: Adapter (port side).
    """

    def new_id(self) -> UUID:
        """Return a new identifier, unique and time-ordered.

        Returns:
            A new UUIDv7.
        """
        ...


class Uuid7Generator:
    """RFC 9562 UUIDv7 generator with a monotonic 42-bit counter.

    Identifiers are strictly increasing for one generator instance, including many
    identifiers within the same millisecond and a wall clock that steps backwards: in
    both cases the last timestamp is reused and the counter incremented. If the counter
    ever rolls over, the timestamp is advanced by one millisecond ahead of the clock,
    which RFC 9562 §6.2 allows, instead of blocking; a frozen clock in a test must not
    hang the generator. Instances are safe to share between threads.

    Implements: Adapter.
    """

    def __init__(
        self,
        clock: Clock | None = None,
        random_source: RandomSource = secrets.token_bytes,
    ) -> None:
        """Create the generator.

        Args:
            clock: Source of the embedded timestamp; ``SystemClock`` by default.
            random_source: Returns the requested number of random bytes. Defaults to
                ``secrets.token_bytes`` because identifiers are published and must
                not be guessable (ADR 0006); tests inject a deterministic source.
        """
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._random_source = random_source
        self._lock = threading.Lock()
        self._last_milliseconds = -1
        self._counter = 0

    def new_id(self) -> UUID:
        """Return the next identifier.

        Returns:
            A UUIDv7 greater than every identifier this instance returned before.

        Raises:
            InvariantViolationError: If the clock returns a naive or pre-1970
                ``datetime``, or the random source returns the wrong number of bytes.
        """
        milliseconds = _milliseconds_since_epoch(self._clock.now())
        random_bytes = self._random_source(RANDOM_BYTES_PER_ID)
        if len(random_bytes) != RANDOM_BYTES_PER_ID:
            message = (
                f"random source returned {len(random_bytes)} bytes, "
                f"expected {RANDOM_BYTES_PER_ID}"
            )
            raise InvariantViolationError(message)
        seed = int.from_bytes(random_bytes[:_COUNTER_SEED_BYTES]) & _COUNTER_SEED_MASK
        tail = int.from_bytes(random_bytes[_COUNTER_SEED_BYTES:])
        with self._lock:
            if milliseconds > self._last_milliseconds:
                self._last_milliseconds = milliseconds
                self._counter = seed
            elif self._counter < _COUNTER_MAX:
                # Same millisecond, or the wall clock went backwards: stay on the last
                # timestamp so ordering never regresses.
                self._counter += 1
            else:
                self._last_milliseconds += 1
                self._counter = seed
            timestamp, counter = self._last_milliseconds, self._counter
        return _assemble(timestamp, counter, tail)


def _milliseconds_since_epoch(moment: datetime) -> int:
    if moment.utcoffset() is None:
        message = "the clock returned a naive datetime; clocks must return UTC"
        raise InvariantViolationError(message)
    milliseconds = (moment - _EPOCH) // _ONE_MILLISECOND
    if milliseconds < 0:
        message = "UUIDv7 cannot encode an instant before 1970-01-01T00:00:00Z"
        raise InvariantViolationError(message, details={"instant": moment.isoformat()})
    return milliseconds


def _assemble(timestamp: int, counter: int, tail: int) -> UUID:
    counter_high = counter >> _COUNTER_LOW_BITS
    counter_low = counter & ((1 << _COUNTER_LOW_BITS) - 1)
    value = (
        (timestamp << 80)
        | _VERSION_FIELD
        | (counter_high << 64)
        | _VARIANT_FIELD
        | (counter_low << _TAIL_BITS)
        | tail
    )
    return UUID(int=value)


def is_uuid7(identifier: UUID) -> bool:
    """Tell whether ``identifier`` has the RFC 9562 variant and version 7.

    Args:
        identifier: Any UUID, for example one generated by an offline client.

    Returns:
        ``True`` if the variant bits are ``0b10`` and the version is 7.
    """
    return identifier.variant == uuid.RFC_4122 and identifier.version == UUID_VERSION_7


def extract_timestamp(identifier: UUID) -> datetime:
    """Return the creation instant embedded in a UUIDv7.

    The embedded time is set by whoever generated the identifier, possibly an offline
    client, so it is never a substitute for reported or observed times (ADR 0006).

    Args:
        identifier: A UUIDv7.

    Returns:
        The embedded instant as a timezone-aware UTC ``datetime``, at millisecond
        precision.

    Raises:
        ValidationError: If ``identifier`` is not a UUIDv7.
    """
    if not is_uuid7(identifier):
        message = "identifier is not a UUIDv7"
        raise ValidationError(message, details={"identifier": str(identifier)})
    return _EPOCH + timedelta(milliseconds=identifier.int >> 80)


def _require_uuid7(identifier: UUID) -> UUID:
    # ValueError, not the kernel's ValidationError: Pydantic only collects ValueError
    # raised inside validators into its own ValidationError.
    if not is_uuid7(identifier):
        message = "identifier must be a UUIDv7 (RFC 9562)"
        raise ValueError(message)
    return identifier


EntityId = Annotated[UUID, AfterValidator(_require_uuid7)]
"""A UUID field that only accepts UUIDv7 values (ADR 0006)."""
