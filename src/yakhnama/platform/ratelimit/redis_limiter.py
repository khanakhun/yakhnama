"""Redis adapter of the ``RateLimiter`` port: a fixed one-minute window (ADR 0017).

One Lua script, run atomically by Redis, increments the key's counter, starts its
60-second expiry on the first request of a window and returns the count and the
milliseconds left. A request is allowed while the count is within the limit.
Fixed windows can let up to twice the limit through around a window boundary; that
is accepted for its single round trip and O(1) memory per key (ADR 0017 compares it
with a sliding window).

If Redis cannot be reached the request is allowed and a warning is logged with the
error type: during a disaster, an unavailable rate limiter must not take the public
record offline with it (ADR 0017, "fail open").

Patterns: Adapter.
"""

import math
from collections.abc import Awaitable
from typing import Final, Protocol

import structlog
from redis.exceptions import RedisError

from yakhnama.platform.ratelimit.limiter import RateLimitDecision

KEY_PREFIX: Final = "yakhnama:ratelimit:"
WINDOW_MILLISECONDS: Final = 60_000
MILLISECONDS_PER_SECOND: Final = 1000
# KEYS[1] = counter key, ARGV[1] = window in milliseconds. The PTTL < 0 branch heals
# a key that lost its expiry (for example after a failed PEXPIRE) so it cannot stay
# blocked forever.
WINDOW_SCRIPT: Final = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('PTTL', KEYS[1])
if ttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


class RedisCommands(Protocol):
    """The one Redis command the limiter needs, so tests can supply a fake.

    ``redis.asyncio.Redis`` satisfies it.

    Implements: Adapter (port side).
    """

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Awaitable[object]:
        """Run a Lua script atomically.

        Args:
            script: The Lua source.
            numkeys: How many of ``keys_and_args`` are keys.
            *keys_and_args: Keys, then arguments.

        Returns:
            An awaitable of the script's reply.
        """
        ...


class RedisRateLimiter:
    """``RateLimiter`` sharing fixed-window counters through Redis.

    Implements: Adapter.
    """

    def __init__(self, redis: RedisCommands) -> None:
        """Create the limiter.

        Args:
            redis: The Redis client; owned and closed by the caller.
        """
        self._redis = redis

    async def check(self, key: str, limit_per_minute: int) -> RateLimitDecision:
        """Count one request in ``key``'s current window.

        Args:
            key: Opaque client key.
            limit_per_minute: Requests allowed per window.

        Returns:
            The decision; allowed with the full limit remaining if Redis fails.
        """
        try:
            reply = await self._redis.eval(
                WINDOW_SCRIPT, 1, KEY_PREFIX + key, str(WINDOW_MILLISECONDS)
            )
            count, ttl_milliseconds = _parse_reply(reply)
        except (RedisError, OSError, ValueError) as error:
            structlog.get_logger(__name__).warning(
                "rate_limiter_unavailable", error_type=type(error).__name__
            )
            return RateLimitDecision(
                is_allowed=True,
                limit=limit_per_minute,
                remaining=limit_per_minute,
                retry_after_seconds=0,
            )
        is_allowed = count <= limit_per_minute
        return RateLimitDecision(
            is_allowed=is_allowed,
            limit=limit_per_minute,
            remaining=max(0, limit_per_minute - count),
            retry_after_seconds=(
                0
                if is_allowed
                else max(1, math.ceil(ttl_milliseconds / MILLISECONDS_PER_SECOND))
            ),
        )


def _parse_reply(reply: object) -> tuple[int, int]:
    """Return ``(count, ttl_milliseconds)`` from the script's reply.

    Raises:
        ValueError: If the reply is not two integers.
    """
    if (
        isinstance(reply, list | tuple)
        and len(reply) == 2  # noqa: PLR2004  # reason: the script returns a pair
        and all(isinstance(item, int) for item in reply)
    ):
        return int(reply[0]), int(reply[1])
    message = "unexpected rate-limit script reply"
    raise ValueError(message)
