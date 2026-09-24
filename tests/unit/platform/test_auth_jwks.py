"""Unit tests for ``HttpJwksClient``: discovery, TTL cache and key rotation.

The identity provider is an ``httpx.MockTransport`` handler; no network is used.
"""

import json
from collections.abc import AsyncIterator, Callable
from datetime import timedelta

import httpx
import pytest
from structlog.testing import capture_logs

from tests.fakes.auth import TEST_ISSUER, TestKeyPair, jwks_document, session_key_pair
from tests.fakes.clock import FrozenClock
from yakhnama.platform.auth.errors import IdentityProviderUnavailableError
from yakhnama.platform.auth.jwks import (
    DISCOVERY_PATH,
    MAX_DOCUMENT_BYTES,
    HttpJwksClient,
    signing_keys_from_jwks,
)

JWKS_URL = "https://identity.test/realms/yakhnama/protocol/openid-connect/certs"
TTL = timedelta(minutes=10)


class FakeProvider:
    """Serves discovery and JWKS documents and counts the requests.

    Implements: Fake (of the identity provider's HTTP endpoints).
    """

    def __init__(self, key_pairs: list[TestKeyPair]) -> None:
        """Serve ``key_pairs``."""
        self.key_pairs = key_pairs
        self.jwks_requests = 0
        self.discovery_requests = 0
        self.discovery: dict[str, object] = {
            "issuer": TEST_ISSUER,
            "jwks_uri": JWKS_URL,
        }
        self.status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one request of the mock transport."""
        if str(request.url) == TEST_ISSUER + DISCOVERY_PATH:
            self.discovery_requests += 1
            return httpx.Response(self.status, json=self.discovery)
        self.jwks_requests += 1
        return httpx.Response(self.status, json=jwks_document(self.key_pairs))


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    clock: FrozenClock,
    *,
    jwks_url: str | None = JWKS_URL,
) -> HttpJwksClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpJwksClient(
        http_client=http_client,
        clock=clock,
        issuer=TEST_ISSUER,
        jwks_url=jwks_url,
        cache_ttl=TTL,
    )


async def test_get_signing_key_known_kid_returns_key(clock: FrozenClock) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)

    key = await client.get_signing_key(key_pair.key_id)

    assert key is not None
    assert key.key_id == key_pair.key_id
    assert provider.jwks_requests == 1


async def test_get_signing_key_within_ttl_uses_cache(clock: FrozenClock) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(TTL - timedelta(seconds=1))

    key = await client.get_signing_key(key_pair.key_id)

    assert key is not None
    assert provider.jwks_requests == 1


async def test_get_signing_key_after_ttl_refetches(clock: FrozenClock) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(TTL)

    await client.get_signing_key(key_pair.key_id)

    assert provider.jwks_requests == 2


async def test_get_signing_key_unknown_kid_refetches_once_and_finds_rotated_key(
    clock: FrozenClock,
) -> None:
    old_key = session_key_pair()
    new_key = TestKeyPair(key_id="rotated-key")
    provider = FakeProvider([old_key])
    client = _client(provider.handle, clock)
    await client.get_signing_key(old_key.key_id)
    provider.key_pairs = [old_key, new_key]
    clock.advance(timedelta(seconds=31))

    key = await client.get_signing_key(new_key.key_id)

    assert key is not None
    assert key.key_id == "rotated-key"
    assert provider.jwks_requests == 2


async def test_get_signing_key_unknown_kid_soon_after_fetch_does_not_refetch(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(timedelta(seconds=5))

    key = await client.get_signing_key("made-up-kid")

    assert key is None
    assert provider.jwks_requests == 1


async def test_get_signing_key_unknown_kid_refetches_only_once_per_interval(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(timedelta(seconds=31))

    first = await client.get_signing_key("made-up-1")
    second = await client.get_signing_key("made-up-2")

    assert (first, second) == (None, None)
    assert provider.jwks_requests == 2


async def test_get_signing_key_failed_forced_refetch_keeps_fresh_cache(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(timedelta(seconds=31))
    provider.status = 503

    unknown = await client.get_signing_key("made-up")
    known = await client.get_signing_key(key_pair.key_id)

    assert unknown is None
    assert known is not None


async def test_get_signing_key_without_jwks_url_discovers_it_once(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock, jwks_url=None)

    await client.get_signing_key(key_pair.key_id)
    clock.advance(TTL)
    key = await client.get_signing_key(key_pair.key_id)

    assert key is not None
    assert provider.discovery_requests == 1
    assert provider.jwks_requests == 2


@pytest.mark.parametrize(
    ("discovery", "reason"),
    [
        (
            {"issuer": "https://other.test", "jwks_uri": JWKS_URL},
            "discovery_issuer_mismatch",
        ),
        (
            {"issuer": TEST_ISSUER, "jwks_uri": "http://evil.test/certs"},
            "insecure_jwks_uri",
        ),
        ({"issuer": TEST_ISSUER}, "invalid_discovery_document"),
    ],
)
async def test_get_signing_key_bad_discovery_raises_unavailable(
    clock: FrozenClock, discovery: dict[str, object], reason: str
) -> None:
    provider = FakeProvider([session_key_pair()])
    provider.discovery = discovery
    client = _client(provider.handle, clock, jwks_url=None)

    with capture_logs() as logs, pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")

    assert {
        "event": "jwks_fetch_failed",
        "reason": reason,
        "log_level": "warning",
    } in logs
    assert provider.jwks_requests == 0


async def test_get_signing_key_http_error_raises_unavailable(
    clock: FrozenClock,
) -> None:
    provider = FakeProvider([session_key_pair()])
    provider.status = 500
    client = _client(provider.handle, clock)

    with capture_logs() as logs, pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")

    assert logs[0]["reason"] == "HTTPStatusError"


async def test_get_signing_key_transport_error_raises_unavailable(
    clock: FrozenClock,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        message = "connection refused"
        raise httpx.ConnectError(message, request=request)

    client = _client(refuse, clock)

    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")


async def test_get_signing_key_non_json_body_raises_unavailable(
    clock: FrozenClock,
) -> None:
    client = _client(lambda _: httpx.Response(200, text="<html>"), clock)

    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")


async def test_get_signing_key_oversized_document_raises_unavailable(
    clock: FrozenClock,
) -> None:
    client = _client(
        lambda _: httpx.Response(200, content=b" " * (MAX_DOCUMENT_BYTES + 1)), clock
    )

    with capture_logs() as logs, pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")

    assert logs[0]["reason"] == "document_too_large"


def test_signing_keys_from_jwks_skips_encryption_and_kidless_keys() -> None:
    key_pair = session_key_pair()
    encryption = TestKeyPair(key_id="enc-key").public_jwk(use="enc")
    kidless = dict(TestKeyPair(key_id="x").public_jwk())
    del kidless["kid"]
    unusable = {"kty": "oct", "kid": "symmetric", "k": "c2VjcmV0"}
    document = {"keys": [key_pair.public_jwk(use=None), encryption, kidless, unusable]}

    keys = signing_keys_from_jwks(document)

    assert set(keys) == {key_pair.key_id}


@pytest.mark.parametrize("document", [[], "keys", {"keys": []}, {"keys": "x"}])
def test_signing_keys_from_jwks_malformed_document_returns_empty(
    document: object,
) -> None:
    keys = signing_keys_from_jwks(document)

    assert dict(keys) == {}


def test_signing_keys_from_jwks_ec_key_is_usable() -> None:
    ec_pair = TestKeyPair(key_id="ec-key", algorithm="ES256")

    keys = signing_keys_from_jwks(jwks_document([ec_pair]))

    assert keys["ec-key"].algorithm_name == "ES256"


async def test_get_signing_key_after_failed_fetch_backs_off_before_retrying(
    clock: FrozenClock,
) -> None:
    provider = FakeProvider([session_key_pair()])
    provider.status = 503
    client = _client(provider.handle, clock)
    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key(session_key_pair().key_id)
    clock.advance(timedelta(seconds=10))

    with capture_logs() as logs, pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key(session_key_pair().key_id)

    assert provider.jwks_requests == 1
    assert logs[0]["reason"] == "refetch_backoff"


async def test_get_signing_key_backoff_ends_after_refetch_interval(
    clock: FrozenClock,
) -> None:
    provider = FakeProvider([session_key_pair()])
    provider.status = 503
    client = _client(provider.handle, clock)
    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key(session_key_pair().key_id)
    provider.status = 200
    clock.advance(timedelta(seconds=30))

    key = await client.get_signing_key(session_key_pair().key_id)

    assert key is not None
    assert provider.jwks_requests == 2


async def test_get_signing_key_failed_refresh_serves_expired_keys_within_grace(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(TTL + timedelta(minutes=1))
    provider.status = 503

    with capture_logs() as logs:
        key = await client.get_signing_key(key_pair.key_id)

    assert key is not None
    assert {
        "event": "jwks_stale_served",
        "age_seconds": int((TTL + timedelta(minutes=1)).total_seconds()),
        "log_level": "warning",
    } in logs


async def test_get_signing_key_during_backoff_serves_expired_keys_within_grace(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(TTL)
    provider.status = 503
    await client.get_signing_key(key_pair.key_id)
    clock.advance(timedelta(seconds=5))

    key = await client.get_signing_key(key_pair.key_id)

    assert key is not None
    assert provider.jwks_requests == 2


async def test_get_signing_key_expired_keys_past_grace_raise_unavailable(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(2 * TTL)
    provider.status = 503

    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key(key_pair.key_id)


async def test_get_signing_key_backoff_past_grace_raises_unavailable(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    provider = FakeProvider([key_pair])
    client = _client(provider.handle, clock)
    await client.get_signing_key(key_pair.key_id)
    clock.advance(2 * TTL - timedelta(seconds=1))
    provider.status = 503
    await client.get_signing_key(key_pair.key_id)
    clock.advance(timedelta(seconds=5))

    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key(key_pair.key_id)

    assert provider.jwks_requests == 2


class CountingBody:
    """An endless chunked body that counts how many chunks were pulled.

    Implements: Fake (of a hostile provider streaming an unbounded document).
    """

    def __init__(self, chunk: bytes, chunks: int) -> None:
        """Stream ``chunks`` copies of ``chunk``."""
        self.chunk = chunk
        self.chunks = chunks
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield the chunks one by one, counting each."""
        for _ in range(self.chunks):
            self.pulled += 1
            yield self.chunk


async def test_get_signing_key_streamed_oversized_body_stops_reading_at_cap(
    clock: FrozenClock,
) -> None:
    chunk_size = 16 * 1024
    body = CountingBody(b" " * chunk_size, chunks=1000)
    client = _client(lambda _: httpx.Response(200, content=body), clock)

    with capture_logs() as logs, pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")

    assert logs[0]["reason"] == "document_too_large"
    # Aborted one chunk past the cap, never the 16 MiB the provider offered.
    assert body.pulled == MAX_DOCUMENT_BYTES // chunk_size + 1


async def test_get_signing_key_declared_oversized_length_reads_nothing(
    clock: FrozenClock,
) -> None:
    body = CountingBody(b"{}", chunks=1)
    client = _client(
        lambda _: httpx.Response(
            200,
            headers={"Content-Length": str(MAX_DOCUMENT_BYTES + 1)},
            content=body,
        ),
        clock,
    )

    with capture_logs() as logs, pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key("any")

    assert logs[0]["reason"] == "document_too_large"
    assert body.pulled == 0


async def test_get_signing_key_streamed_body_within_configured_cap_is_parsed(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    document = json.dumps(jwks_document([key_pair])).encode()
    body = CountingBody(document, chunks=1)
    client = HttpJwksClient(
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
        ),
        clock=clock,
        issuer=TEST_ISSUER,
        jwks_url=JWKS_URL,
        cache_ttl=TTL,
        max_document_bytes=len(document),
    )

    key = await client.get_signing_key(key_pair.key_id)

    assert key is not None


async def test_get_signing_key_body_one_byte_over_configured_cap_raises(
    clock: FrozenClock,
) -> None:
    key_pair = session_key_pair()
    document = json.dumps(jwks_document([key_pair])).encode()
    client = HttpJwksClient(
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=CountingBody(document, 1))
            )
        ),
        clock=clock,
        issuer=TEST_ISSUER,
        jwks_url=JWKS_URL,
        cache_ttl=TTL,
        max_document_bytes=len(document) - 1,
    )

    with pytest.raises(IdentityProviderUnavailableError):
        await client.get_signing_key(key_pair.key_id)
