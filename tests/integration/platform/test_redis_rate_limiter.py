"""``RedisRateLimiter`` against a real Redis testcontainer."""

import asyncio

import pytest
from redis.asyncio import Redis

from yakhnama.platform.ratelimit.redis_limiter import (
    KEY_PREFIX,
    WINDOW_MILLISECONDS,
    RedisRateLimiter,
)

pytestmark = pytest.mark.integration


async def test_redis_limiter_allows_limit_then_refuses_with_retry_after(
    redis_client: Redis,
) -> None:
    limiter = RedisRateLimiter(redis_client)

    decisions = [await limiter.check("ip:a", 3) for _ in range(4)]

    assert [decision.is_allowed for decision in decisions] == [True, True, True, False]
    assert [decision.remaining for decision in decisions] == [2, 1, 0, 0]
    assert 1 <= decisions[-1].retry_after_seconds <= WINDOW_MILLISECONDS // 1000


async def test_redis_limiter_sets_window_expiry_on_first_request(
    redis_client: Redis,
) -> None:
    limiter = RedisRateLimiter(redis_client)

    await limiter.check("ip:b", 3)

    ttl = await redis_client.pttl(KEY_PREFIX + "ip:b")
    assert 0 < ttl <= WINDOW_MILLISECONDS


async def test_redis_limiter_window_reset_allows_again(redis_client: Redis) -> None:
    limiter = RedisRateLimiter(redis_client)
    await limiter.check("ip:c", 1)
    refused = await limiter.check("ip:c", 1)
    # Shorten the window instead of waiting a minute: the script must honour the
    # key's own expiry.
    await redis_client.pexpire(KEY_PREFIX + "ip:c", 50)
    await asyncio.sleep(0.1)

    allowed = await limiter.check("ip:c", 1)

    assert refused.is_allowed is False
    assert allowed.is_allowed is True


async def test_redis_limiter_key_without_expiry_is_healed(redis_client: Redis) -> None:
    await redis_client.set(KEY_PREFIX + "ip:d", 5)
    limiter = RedisRateLimiter(redis_client)

    decision = await limiter.check("ip:d", 1)

    ttl = await redis_client.pttl(KEY_PREFIX + "ip:d")
    assert decision.is_allowed is False
    assert 0 < ttl <= WINDOW_MILLISECONDS


async def test_redis_limiter_concurrent_requests_are_counted_atomically(
    redis_client: Redis,
) -> None:
    limiter = RedisRateLimiter(redis_client)

    decisions = await asyncio.gather(*(limiter.check("ip:e", 10) for _ in range(25)))

    assert sum(decision.is_allowed for decision in decisions) == 10
