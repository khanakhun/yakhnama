"""The ``RateLimiter`` port, its decision DTO and the in-memory token bucket.

``InMemoryRateLimiter`` gives every key a bucket of ``limit_per_minute`` tokens that
refills continuously at ``limit_per_minute / 60`` tokens per second; a request takes
one token or is refused with the time until the next token. The number of buckets is
capped and the least recently used bucket is evicted first, so a flood of distinct
client IPs cannot grow memory without bound. State lives in one process only, so this
adapter is for development and tests; production uses ``RedisRateLimiter``
(ADR 0017).

Patterns: Adapter (the port and the in-memory adapter), DTO (the decision).
"""

import math
from collections import OrderedDict
from datetime import datetime
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.shared_kernel.clock import Clock

SECONDS_PER_MINUTE: Final = 60
DEFAULT_MAX_KEYS: Final = 10_000


class RateLimitDecision(BaseModel):
    """Whether one request may proceed, and what the client should be told.

    Implements: DTO.

    Attributes:
        is_allowed: Whether the request is within the limit.
        limit: The limit that was applied, per minute.
        remaining: Requests left before the limit is reached.
        retry_after_seconds: Whole seconds until a request would be allowed again;
            0 when allowed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_allowed: bool
    limit: int = Field(ge=1)
    remaining: int = Field(ge=0)
    retry_after_seconds: int = Field(ge=0)


class RateLimiter(Protocol):
    """Counts requests per key against a per-minute limit.

    Implements: Adapter (port side).
    """

    async def check(self, key: str, limit_per_minute: int) -> RateLimitDecision:
        """Record one request for ``key`` and decide whether it may proceed.

        Args:
            key: Opaque client key, for example ``"ip:<hash>"``.
            limit_per_minute: Requests allowed per minute for this key.

        Returns:
            The decision.
        """
        ...


class _Bucket(BaseModel):
    """The token count of one key at one instant.

    Implements: Value Object.

    Attributes:
        tokens: Tokens left, fractional while refilling.
        updated_at: When ``tokens`` was computed (UTC).
    """

    model_config = ConfigDict(frozen=True)

    tokens: float
    updated_at: datetime


class InMemoryRateLimiter:
    """``RateLimiter`` with a token bucket per key, in process memory.

    Implements: Adapter.
    """

    def __init__(self, clock: Clock, *, max_keys: int = DEFAULT_MAX_KEYS) -> None:
        """Create the limiter.

        Args:
            clock: Source of the current instant; frozen in tests.
            max_keys: Most buckets kept; the least recently used is evicted.

        Raises:
            ValueError: If ``max_keys`` is below 1.
        """
        if max_keys < 1:
            message = "max_keys must be at least 1"
            raise ValueError(message)
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    async def check(self, key: str, limit_per_minute: int) -> RateLimitDecision:
        """Take one token from ``key``'s bucket if there is one.

        Args:
            key: Opaque client key.
            limit_per_minute: Bucket capacity and tokens refilled per minute.

        Returns:
            The decision; a refused request takes no token.
        """
        now = self._clock.now()
        capacity = float(limit_per_minute)
        refill_per_second = capacity / SECONDS_PER_MINUTE
        bucket = self._buckets.pop(key, None)
        if bucket is None:
            tokens = capacity
        else:
            elapsed = max(0.0, (now - bucket.updated_at).total_seconds())
            tokens = min(capacity, bucket.tokens + elapsed * refill_per_second)
        is_allowed = tokens >= 1.0
        if is_allowed:
            tokens -= 1.0
        self._buckets[key] = _Bucket(tokens=tokens, updated_at=now)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        retry_after = 0 if is_allowed else math.ceil((1.0 - tokens) / refill_per_second)
        return RateLimitDecision(
            is_allowed=is_allowed,
            limit=limit_per_minute,
            remaining=math.floor(tokens),
            retry_after_seconds=retry_after,
        )
