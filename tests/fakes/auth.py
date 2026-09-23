"""Test doubles for authentication, rate limiting and idempotency.

- ``TestKeyPair`` generates a signing key in process memory (RSA 2048 or EC P-256);
  it is never written to disk and never committed. ``session_key_pair`` returns one
  cached RSA pair per test session, because key generation is slow.
- ``issue_token`` signs claims with a key pair; ``access_token_claims`` builds a
  valid claim set for a principal, and every claim can be overridden.
- ``FakeJwksClient`` serves the test keys to ``TokenValidator`` without a network.
- ``StaticRateLimiter`` returns a fixed decision and records every call.
- ``InMemoryIdempotencyStore`` implements the ``IdempotencyStore`` port in a dict.

Wiring for API tests (the api-engineer's ``tests/api``)::

    key_pair = session_key_pair()
    validator = TokenValidator(
        jwks_client=FakeJwksClient([key_pair]),
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        algorithms=["RS256"],
        leeway_seconds=0,
        clock=clock,
    )
    container = dataclasses.replace(
        build_container(settings),
        token_validator=validator,
        idempotency_store=InMemoryIdempotencyStore(),
    )
    token = issue_token(access_token_claims(subject="user-1"), key_pair)

Patterns: Fake.
"""

import functools
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from yakhnama.platform.idempotency.store import (
    IdempotencyRecord,
    IdempotencyReservation,
    StoredResponse,
)
from yakhnama.platform.ratelimit.limiter import RateLimitDecision

TEST_ISSUER: Final = "https://identity.test/realms/yakhnama"
TEST_AUDIENCE: Final = "yakhnama-api"
TEST_KEY_ID: Final = "test-key-1"
RSA_KEY_BITS: Final = 2048
RSA_PUBLIC_EXPONENT: Final = 65537
TOKEN_LIFETIME: Final = timedelta(minutes=5)
# Equal to FIXED_NOW in tests/conftest.py, the instant the clock fixture is frozen at.
DEFAULT_ISSUED_AT: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

TestAlgorithm = Literal["RS256", "ES256"]


class TestKeyPair:
    """A signing key generated in memory, with its public JWK.

    Implements: Fake (of the identity provider's signing key).

    Attributes:
        key_id: The ``kid`` published in the JWK and put in token headers.
        algorithm: ``RS256`` or ``ES256``.
    """

    # Not a test class, although its name starts with "Test".
    __test__ = False

    def __init__(
        self, key_id: str = TEST_KEY_ID, algorithm: TestAlgorithm = "RS256"
    ) -> None:
        """Generate the key.

        Args:
            key_id: The ``kid``.
            algorithm: ``RS256`` (RSA 2048) or ``ES256`` (P-256).
        """
        self.key_id = key_id
        self.algorithm: TestAlgorithm = algorithm
        self._private_key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey = (
            rsa.generate_private_key(
                public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_BITS
            )
            if algorithm == "RS256"
            else ec.generate_private_key(ec.SECP256R1())
        )

    @property
    def private_key(self) -> rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey:
        """The private key, for signing only."""
        return self._private_key

    def public_jwk(self, *, use: str | None = "sig") -> dict[str, object]:
        """Return the public key as a JWK, as a provider publishes it.

        Args:
            use: The JWK ``use``; ``None`` leaves it out.

        Returns:
            The JWK members.
        """
        public_key = self._private_key.public_key()
        members = (
            jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
            if isinstance(public_key, rsa.RSAPublicKey)
            else jwt.algorithms.ECAlgorithm.to_jwk(public_key, as_dict=True)
        )
        jwk: dict[str, object] = dict(members)
        jwk["kid"] = self.key_id
        jwk["alg"] = self.algorithm
        if use is not None:
            jwk["use"] = use
        return jwk

    def pyjwk(self) -> jwt.PyJWK:
        """Return the public key as PyJWT's ``PyJWK``."""
        return jwt.PyJWK(self.public_jwk())


@functools.cache
def session_key_pair() -> TestKeyPair:
    """Return one RSA key pair shared by the whole test session."""
    return TestKeyPair()


def jwks_document(key_pairs: Iterable[TestKeyPair]) -> dict[str, object]:
    """Return a JWKS document publishing the key pairs' public keys.

    Args:
        key_pairs: The keys to publish.

    Returns:
        ``{"keys": [...]}``.
    """
    return {"keys": [key_pair.public_jwk() for key_pair in key_pairs]}


def access_token_claims(  # noqa: PLR0913  # reason: every claim is a test knob
    *,
    subject: str = "test-subject",
    issuer: str = TEST_ISSUER,
    audience: str | list[str] = TEST_AUDIENCE,
    now: datetime | None = None,
    lifetime: timedelta = TOKEN_LIFETIME,
    realm_roles: Iterable[str] = (),
    name: str | None = None,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return the claims of a valid access token, Keycloak style.

    Args:
        subject: ``sub``.
        issuer: ``iss``.
        audience: ``aud``.
        now: The issue instant; ``FIXED_NOW`` of ``tests/conftest.py`` when
            ``None``, matching the ``clock`` fixture the validator is given.
        lifetime: ``exp`` minus ``iat``.
        realm_roles: ``realm_access.roles``.
        name: ``name``, when given.
        overrides: Claims to set or replace last; a value of ``None`` removes the
            claim.

    Returns:
        The claim set.
    """
    issued_at = now if now is not None else DEFAULT_ISSUED_AT
    claims: dict[str, object] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + lifetime).timestamp()),
        "jti": f"jti-{subject}",
        "realm_access": {"roles": list(realm_roles)},
    }
    if name is not None:
        claims["name"] = name
    for claim, value in (overrides or {}).items():
        if value is None:
            claims.pop(claim, None)
        else:
            claims[claim] = value
    return claims


def issue_token(
    claims: Mapping[str, object],
    key_pair: TestKeyPair,
    *,
    kid: str | None = None,
    headers: Mapping[str, object] | None = None,
) -> str:
    """Sign ``claims`` with ``key_pair``.

    Args:
        claims: The payload.
        key_pair: The signing key; its algorithm is used.
        kid: The ``kid`` header; the key pair's own id when ``None``.
        headers: Extra or replacing JOSE header members.

    Returns:
        The compact JWS.
    """
    header: dict[str, object] = {"kid": kid if kid is not None else key_pair.key_id}
    header.update(headers or {})
    return jwt.encode(
        dict(claims),
        key_pair.private_key,
        algorithm=key_pair.algorithm,
        headers=header,
    )


class FakeJwksClient:
    """``JwksClient`` serving in-memory test keys.

    Implements: Fake (of ``JwksClient``).

    Attributes:
        requested_key_ids: Every ``kid`` asked for, in order.
    """

    def __init__(self, key_pairs: Iterable[TestKeyPair] = ()) -> None:
        """Publish the given keys.

        Args:
            key_pairs: The keys to serve.
        """
        self._keys = {key_pair.key_id: key_pair.pyjwk() for key_pair in key_pairs}
        self.requested_key_ids: list[str] = []

    def publish(self, key_pair: TestKeyPair) -> None:
        """Add a key, as a provider does when it rotates.

        Args:
            key_pair: The new key.
        """
        self._keys[key_pair.key_id] = key_pair.pyjwk()

    async def get_signing_key(self, key_id: str) -> jwt.PyJWK | None:
        """Return the published key ``key_id``.

        Args:
            key_id: The token's ``kid``.

        Returns:
            The key, or ``None`` if not published.
        """
        self.requested_key_ids.append(key_id)
        return self._keys.get(key_id)


class StaticRateLimiter:
    """``RateLimiter`` that always gives the same decision.

    Implements: Fake (of ``RateLimiter``).

    Attributes:
        calls: ``(key, limit_per_minute)`` of every check.
    """

    def __init__(
        self, *, is_allowed: bool = True, retry_after_seconds: int = 30
    ) -> None:
        """Fix the decision.

        Args:
            is_allowed: Whether every request is allowed.
            retry_after_seconds: ``Retry-After`` of a refusal.
        """
        self._is_allowed = is_allowed
        self._retry_after_seconds = retry_after_seconds
        self.calls: list[tuple[str, int]] = []

    async def check(self, key: str, limit_per_minute: int) -> RateLimitDecision:
        """Record the call and return the fixed decision.

        Args:
            key: The client key.
            limit_per_minute: The limit applied.

        Returns:
            The decision.
        """
        self.calls.append((key, limit_per_minute))
        return RateLimitDecision(
            is_allowed=self._is_allowed,
            limit=limit_per_minute,
            remaining=limit_per_minute - 1 if self._is_allowed else 0,
            retry_after_seconds=0 if self._is_allowed else self._retry_after_seconds,
        )


class _Entry:
    """One stored record.

    Implements: Fake (row of ``InMemoryIdempotencyStore``).
    """

    def __init__(self, reservation: IdempotencyReservation) -> None:
        self.request_hash = reservation.request_hash
        self.response: StoredResponse | None = None
        self.expires_at = reservation.expires_at

    def record(self) -> IdempotencyRecord:
        return IdempotencyRecord(
            request_hash=self.request_hash,
            response=self.response,
            expires_at=self.expires_at,
        )


class InMemoryIdempotencyStore:
    """``IdempotencyStore`` over a dict, with the PostgreSQL adapter's rules.

    Implements: Fake (of ``IdempotencyStore``).

    Attributes:
        released: ``(scope, key)`` of every release, in order.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._entries: dict[tuple[str, UUID], _Entry] = {}
        self.released: list[tuple[str, UUID]] = []

    async def reserve(
        self, reservation: IdempotencyReservation
    ) -> IdempotencyRecord | None:
        """Claim ``(scope, key)``; an expired entry counts as absent.

        Args:
            reservation: The claim.

        Returns:
            ``None`` if claimed, else the live record.
        """
        slot = (reservation.scope, reservation.key)
        existing = self._entries.get(slot)
        if existing is not None and existing.expires_at > reservation.created_at:
            return existing.record()
        self._entries[slot] = _Entry(reservation)
        return None

    async def complete(
        self, scope: str, key: UUID, response: StoredResponse, expires_at: datetime
    ) -> None:
        """Store the response and extend the entry.

        Args:
            scope: The scope.
            key: The key.
            response: The response.
            expires_at: The new expiry.
        """
        entry = self._entries.get((scope, key))
        if entry is not None:
            entry.response = response
            entry.expires_at = expires_at

    async def release(self, scope: str, key: UUID) -> None:
        """Delete a pending entry.

        Args:
            scope: The scope.
            key: The key.
        """
        self.released.append((scope, key))
        entry = self._entries.get((scope, key))
        if entry is not None and entry.response is None:
            del self._entries[(scope, key)]

    async def purge_expired(self, now: datetime) -> int:
        """Delete lapsed entries.

        Args:
            now: The current instant.

        Returns:
            How many were deleted.
        """
        expired = [
            slot for slot, entry in self._entries.items() if entry.expires_at <= now
        ]
        for slot in expired:
            del self._entries[slot]
        return len(expired)

    def stored(self, scope: str, key: UUID) -> IdempotencyRecord | None:
        """Return the entry for ``(scope, key)`` for assertions.

        Args:
            scope: The scope.
            key: The key.

        Returns:
            The record, or ``None``.
        """
        entry = self._entries.get((scope, key))
        return None if entry is None else entry.record()
