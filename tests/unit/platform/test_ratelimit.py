"""Unit tests for the rate limiters and the rate-limit middleware."""

from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from starlette.types import Receive, Scope, Send
from structlog.testing import capture_logs

from tests.fakes.auth import StaticRateLimiter
from tests.fakes.clock import FrozenClock
from tests.unit.platform.asgi import RecordingApp, client_for
from yakhnama.platform.auth.principal import Principal
from yakhnama.platform.auth.resolution import PrincipalResolution
from yakhnama.platform.ratelimit.limiter import InMemoryRateLimiter, RateLimitDecision
from yakhnama.platform.ratelimit.middleware import (
    RateLimitMiddleware,
    client_key,
    rate_limit_headers,
)
from yakhnama.platform.ratelimit.redis_limiter import (
    KEY_PREFIX,
    WINDOW_MILLISECONDS,
    WINDOW_SCRIPT,
    RedisRateLimiter,
)
from yakhnama.platform.request_state import PRINCIPAL_RESOLUTION_STATE_KEY, state_of

PRINCIPAL = Principal(
    subject="user-1",
    issuer="https://identity.test",
    expires_at=datetime(2026, 9, 23, 13, 0, tzinfo=UTC),
)


class FakeRedis:
    """``RedisCommands`` that emulates the window script in memory.

    Implements: Fake (of ``RedisCommands``).

    Attributes:
        calls: ``(script, numkeys, keys_and_args)`` of every call.
    """

    def __init__(self, *, reply: object = None, error: Exception | None = None) -> None:
        """Emulate the script, or answer ``reply``, or raise ``error``."""
        self._counts: dict[str, int] = {}
        self._reply = reply
        self._error = error
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Awaitable[object]:
        """Record the call and return an awaitable of the reply."""
        self.calls.append((script, numkeys, keys_and_args))

        async def run() -> object:
            if self._error is not None:
                raise self._error
            if self._reply is not None:
                return self._reply
            key = keys_and_args[0]
            self._counts[key] = self._counts.get(key, 0) + 1
            return [self._counts[key], 42_500]

        return run()


async def test_in_memory_limiter_allows_up_to_capacity_then_refuses(
    clock: FrozenClock,
) -> None:
    limiter = InMemoryRateLimiter(clock)

    decisions = [await limiter.check("ip:a", 3) for _ in range(4)]

    assert [decision.is_allowed for decision in decisions] == [True, True, True, False]
    assert [decision.remaining for decision in decisions] == [2, 1, 0, 0]
    assert decisions[-1].retry_after_seconds == 20
    assert decisions[0].retry_after_seconds == 0


async def test_in_memory_limiter_refills_continuously(clock: FrozenClock) -> None:
    limiter = InMemoryRateLimiter(clock)
    for _ in range(60):
        await limiter.check("ip:a", 60)
    refused = await limiter.check("ip:a", 60)
    clock.advance(timedelta(seconds=1))

    allowed = await limiter.check("ip:a", 60)

    assert refused.is_allowed is False
    assert refused.retry_after_seconds == 1
    assert allowed.is_allowed is True


async def test_in_memory_limiter_refill_never_exceeds_capacity(
    clock: FrozenClock,
) -> None:
    limiter = InMemoryRateLimiter(clock)
    await limiter.check("ip:a", 5)
    clock.advance(timedelta(hours=1))

    decision = await limiter.check("ip:a", 5)

    assert decision.remaining == 4


async def test_in_memory_limiter_keys_are_independent(clock: FrozenClock) -> None:
    limiter = InMemoryRateLimiter(clock)
    await limiter.check("ip:a", 1)

    other = await limiter.check("ip:b", 1)

    assert other.is_allowed is True


async def test_in_memory_limiter_evicts_least_recently_used_key(
    clock: FrozenClock,
) -> None:
    limiter = InMemoryRateLimiter(clock, max_keys=2)
    await limiter.check("ip:a", 1)
    await limiter.check("ip:b", 1)
    await limiter.check("ip:a", 1)
    await limiter.check("ip:c", 1)

    evicted_b = await limiter.check("ip:b", 1)
    kept_a = await limiter.check("ip:c", 1)

    assert evicted_b.is_allowed is True
    assert kept_a.is_allowed is False


def test_in_memory_limiter_zero_max_keys_raises(clock: FrozenClock) -> None:
    with pytest.raises(ValueError, match="max_keys"):
        InMemoryRateLimiter(clock, max_keys=0)


async def test_redis_limiter_counts_in_window_then_refuses() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis)

    decisions = [await limiter.check("ip:a", 2) for _ in range(3)]

    assert [decision.is_allowed for decision in decisions] == [True, True, False]
    assert [decision.remaining for decision in decisions] == [1, 0, 0]
    assert decisions[-1].retry_after_seconds == 43
    assert redis.calls[0] == (
        WINDOW_SCRIPT,
        1,
        (KEY_PREFIX + "ip:a", str(WINDOW_MILLISECONDS)),
    )


async def test_redis_limiter_refusal_at_window_end_waits_one_second() -> None:
    limiter = RedisRateLimiter(FakeRedis(reply=[5, 0]))

    decision = await limiter.check("ip:a", 1)

    assert decision.retry_after_seconds == 1


@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("down"),
        RedisTimeoutError("slow"),
        TimeoutError("socket timed out"),
        OSError("unreachable"),
    ],
)
async def test_redis_limiter_unavailable_fails_open_and_logs_type(
    error: Exception,
) -> None:
    limiter = RedisRateLimiter(FakeRedis(error=error))

    with capture_logs() as logs:
        decision = await limiter.check("ip:a", 10)

    assert decision == RateLimitDecision(
        is_allowed=True, limit=10, remaining=10, retry_after_seconds=0
    )
    assert logs[0]["event"] == "rate_limiter_unavailable"
    assert logs[0]["error_type"] == type(error).__name__


@pytest.mark.parametrize("reply", [[1], "ok", [1, "x"], (1, 2, 3)])
async def test_redis_limiter_unexpected_reply_fails_open(reply: object) -> None:
    limiter = RedisRateLimiter(FakeRedis(reply=reply))

    decision = await limiter.check("ip:a", 10)

    assert decision.is_allowed is True


class ResolvedAs:
    """Sets a fixed principal resolution, like the resolution middleware.

    Implements: Fake (of principal resolution).
    """

    def __init__(self, app: RateLimitMiddleware, principal: Principal | None) -> None:
        """Wrap ``app``."""
        self._app = app
        self._principal = principal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Store the resolution and call the middleware."""
        if scope["type"] == "http":
            state_of(scope)[PRINCIPAL_RESOLUTION_STATE_KEY] = PrincipalResolution(
                principal=self._principal
            )
        await self._app(scope, receive, send)


def _middleware(
    limiter: StaticRateLimiter, inner: RecordingApp, principal: Principal | None
) -> ResolvedAs:
    return ResolvedAs(
        RateLimitMiddleware(
            inner,
            limiter=limiter,
            anonymous_per_minute=60,
            authenticated_per_minute=300,
        ),
        principal,
    )


async def test_rate_limit_middleware_allowed_request_gets_limit_headers() -> None:
    limiter = StaticRateLimiter()
    inner = RecordingApp()

    async with client_for(_middleware(limiter, inner, None)) as client:
        response = await client.get("/api/v1/hazard-types")

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "60"
    assert response.headers["x-ratelimit-remaining"] == "59"
    assert "retry-after" not in response.headers


async def test_rate_limit_middleware_refused_request_gets_429_problem() -> None:
    limiter = StaticRateLimiter(is_allowed=False, retry_after_seconds=17)
    inner = RecordingApp()

    async with client_for(_middleware(limiter, inner, None)) as client:
        response = await client.get("/api/v1/hazard-types")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
    assert response.headers["x-ratelimit-remaining"] == "0"
    assert response.json()["type"] == "https://yakhnama.org/problems/rate-limited"
    assert inner.calls == 0


async def test_rate_limit_middleware_anonymous_is_keyed_by_hashed_ip() -> None:
    limiter = StaticRateLimiter()

    async with client_for(_middleware(limiter, RecordingApp(), None)) as client:
        await client.get("/api/v1/x")

    key, limit = limiter.calls[0]
    assert key.startswith("ip:")
    assert "127.0.0.1" not in key
    assert limit == 60


async def test_rate_limit_middleware_authenticated_is_keyed_by_principal() -> None:
    limiter = StaticRateLimiter()

    async with client_for(_middleware(limiter, RecordingApp(), PRINCIPAL)) as client:
        await client.get("/api/v1/x")

    assert limiter.calls == [(f"principal:{PRINCIPAL.scope_key()}", 300)]


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
async def test_rate_limit_middleware_health_probes_are_not_counted(path: str) -> None:
    limiter = StaticRateLimiter(is_allowed=False)
    inner = RecordingApp()

    async with client_for(_middleware(limiter, inner, None)) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert limiter.calls == []


def test_client_key_without_client_address_uses_unknown_bucket() -> None:
    key = client_key({"type": "http", "client": None})

    assert key.startswith("ip:")


def test_rate_limit_headers_refused_decision_includes_retry_after() -> None:
    decision = RateLimitDecision(
        is_allowed=False, limit=5, remaining=0, retry_after_seconds=9
    )

    headers = rate_limit_headers(decision)

    assert headers == {
        "X-RateLimit-Limit": "5",
        "X-RateLimit-Remaining": "0",
        "Retry-After": "9",
    }
