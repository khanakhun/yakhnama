"""The ``JwksClient`` port and its HTTP adapter with a TTL cache (ADR 0015).

Signing keys are fetched from the identity provider's JWKS endpoint, cached for
``jwks_cache_ttl_seconds`` and fetched again when a token names a ``kid`` the cache
does not hold, which is how a key rotation reaches the API without a restart. That
forced refetch is rate-limited to one per ``UNKNOWN_KID_REFETCH_INTERVAL``, so a
stream of tokens with made-up ``kid`` values cannot turn the API into a load
generator against the provider. The same interval is a backoff after a failed
fetch, and expired keys are served for one more TTL while the provider is down.

When the JWKS URL is not configured it is discovered once from
``<issuer>/.well-known/openid-configuration`` (OpenID Connect Discovery 1.0), whose
``issuer`` must equal the configured issuer exactly.

Nothing here logs a token or a key; a failed fetch is logged with the error type
only.

Patterns: Adapter (the ``JwksClient`` port and ``HttpJwksClient``),
Anti-Corruption Layer (the discovery document model).
"""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final, Protocol

import httpx
import jwt
import structlog
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from yakhnama.platform.auth.errors import IdentityProviderUnavailableError
from yakhnama.platform.settings import is_secure_or_loopback_url
from yakhnama.shared_kernel.clock import Clock

DISCOVERY_PATH: Final = "/.well-known/openid-configuration"
UNKNOWN_KID_REFETCH_INTERVAL: Final = timedelta(seconds=30)
# A real JWKS is a few kilobytes; the cap keeps a misbehaving endpoint from making
# the API parse megabytes on the request path.
MAX_DOCUMENT_BYTES: Final = 256 * 1024
# Keys marked for encryption ("enc") must never verify a signature (RFC 7517 §4.2).
_SIGNATURE_USES: Final = frozenset({None, "sig"})
# Only public-key types: a symmetric ("oct") key published by mistake would be a
# shared secret, and the allow-list algorithms need RSA or EC keys anyway.
_PUBLIC_KEY_TYPES: Final = frozenset({"RSA", "EC"})
_UNAVAILABLE_MESSAGE: Final = "The identity provider is unavailable; try again later."
# Logged reasons for a failed fetch; they name the failure, never the data.
INVALID_DISCOVERY_DOCUMENT: Final = "invalid_discovery_document"
DISCOVERY_ISSUER_MISMATCH: Final = "discovery_issuer_mismatch"
INSECURE_JWKS_URI: Final = "insecure_jwks_uri"
DOCUMENT_TOO_LARGE: Final = "document_too_large"
REFETCH_BACKOFF: Final = "refetch_backoff"


class JwksClient(Protocol):
    """Source of the identity provider's public signing keys.

    Implements: Adapter (port side).
    """

    async def get_signing_key(self, key_id: str) -> jwt.PyJWK | None:
        """Return the signing key with id ``key_id``.

        Args:
            key_id: The ``kid`` header of the token being checked.

        Returns:
            The key, or ``None`` if the provider does not publish it.

        Raises:
            IdentityProviderUnavailableError: If the keys cannot be fetched.
        """
        ...


class DiscoveryDocument(BaseModel):
    """The two fields of an OpenID provider configuration the API relies on.

    Implements: Anti-Corruption Layer (OpenID Connect Discovery 1.0 document).

    Attributes:
        issuer: The provider's issuer identifier.
        jwks_uri: Where the provider publishes its signing keys.
    """

    # Providers publish dozens of other fields; they are none of our business.
    model_config = ConfigDict(frozen=True, extra="ignore")

    issuer: str = Field(min_length=1, max_length=2048)
    jwks_uri: str = Field(min_length=1, max_length=2048)


class _CachedKeys(BaseModel):
    """The keys of one fetch and when they were fetched.

    Implements: Value Object.

    Attributes:
        keys: Signing keys by ``kid``.
        fetched_at: When the JWKS was fetched (UTC).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    keys: Mapping[str, jwt.PyJWK]
    fetched_at: datetime


def signing_keys_from_jwks(document: object) -> Mapping[str, jwt.PyJWK]:
    """Parse a JWKS document into its usable signing keys by ``kid``.

    Keys without a ``kid``, keys for encryption, symmetric keys and keys PyJWT cannot
    use (unknown ``kty`` or curve) are skipped.

    Args:
        document: The decoded JSON body of the JWKS endpoint.

    Returns:
        A read-only mapping from ``kid`` to key; empty when nothing is usable.
    """
    if not isinstance(document, dict):
        return MappingProxyType({})
    try:
        key_set = jwt.PyJWKSet.from_dict(document)
    except jwt.PyJWTError:
        return MappingProxyType({})
    return MappingProxyType(
        {
            key.key_id: key
            for key in key_set.keys
            if key.key_id
            and key.public_key_use in _SIGNATURE_USES
            and key.key_type in _PUBLIC_KEY_TYPES
        }
    )


class HttpJwksClient:
    """``JwksClient`` that fetches the JWKS over HTTP and caches it with a TTL.

    One instance is shared by every request of an application. Fetches are
    serialised by a lock, so a burst of requests after the cache expires triggers
    one fetch, not one per request.

    Implements: Adapter.
    """

    def __init__(  # noqa: PLR0913  # reason: keyword-only wiring from settings
        self,
        *,
        http_client: httpx.AsyncClient,
        clock: Clock,
        issuer: str,
        jwks_url: str | None,
        cache_ttl: timedelta,
        refetch_interval: timedelta = UNKNOWN_KID_REFETCH_INTERVAL,
    ) -> None:
        """Create the client; no request is made until a key is needed.

        Args:
            http_client: The HTTP client, with its timeout already set. It is owned
                by the caller, which closes it.
            clock: Source of the current instant for the cache.
            issuer: The configured issuer, for discovery and its check.
            jwks_url: The JWKS endpoint, or ``None`` to discover it.
            cache_ttl: How long a fetched key set is trusted.
            refetch_interval: Minimum time between two fetches forced by an
                unknown ``kid``.
        """
        self._http_client = http_client
        self._clock = clock
        self._issuer = issuer
        self._jwks_url = jwks_url
        self._cache_ttl = cache_ttl
        self._refetch_interval = refetch_interval
        self._cached: _CachedKeys | None = None
        self._last_attempt_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_signing_key(self, key_id: str) -> jwt.PyJWK | None:
        """Return the signing key ``key_id``, fetching the JWKS when needed.

        The cache is used while it is younger than the TTL. An unknown ``kid``
        triggers one refetch. No fetch is attempted while the last attempt,
        successful or not, is more recent than the refetch interval (backoff).
        When a refresh of an expired cache fails, or is skipped by the backoff, the
        expired keys are still served for one more TTL (``jwks_stale_served``);
        after that the provider counts as unavailable.

        Args:
            key_id: The ``kid`` header of the token being checked.

        Returns:
            The key, or ``None`` if the provider does not publish it.

        Raises:
            IdentityProviderUnavailableError: If the keys cannot be fetched and no
                cache within its TTL plus the grace period exists.
        """
        async with self._lock:
            now = self._clock.now()
            cached = self._cached
            if cached is None or now - cached.fetched_at >= self._cache_ttl:
                cached = await self._refresh_expired(now, cached)
            key = cached.keys.get(key_id)
            if key is not None or not self._may_force_refetch(now):
                return key
            try:
                cached = await self._refresh(now)
            except IdentityProviderUnavailableError:
                # The cache is still usable, so the provider being down says
                # nothing about this token except that its key is not known.
                return None
            return cached.keys.get(key_id)

    async def _refresh_expired(
        self, now: datetime, stale: _CachedKeys | None
    ) -> _CachedKeys:
        if self._may_force_refetch(now):
            try:
                return await self._refresh(now)
            except IdentityProviderUnavailableError:
                usable = self._within_grace(now, stale)
                if usable is None:
                    raise
        else:
            usable = self._within_grace(now, stale)
            if usable is None:
                raise self._unavailable(REFETCH_BACKOFF)
        structlog.get_logger(__name__).warning(
            "jwks_stale_served",
            age_seconds=int((now - usable.fetched_at).total_seconds()),
        )
        return usable

    def _within_grace(
        self, now: datetime, stale: _CachedKeys | None
    ) -> _CachedKeys | None:
        # One more TTL after expiry: long enough to ride out a provider restart,
        # short enough that a withdrawn key stops working within two TTLs.
        if stale is not None and now - stale.fetched_at < 2 * self._cache_ttl:
            return stale
        return None

    def _may_force_refetch(self, now: datetime) -> bool:
        # Measured from the last attempt, successful or not, so an outage does not
        # turn every unknown kid into another request to the provider.
        last = self._last_attempt_at
        return last is None or now - last >= self._refetch_interval

    async def _refresh(self, now: datetime) -> _CachedKeys:
        self._last_attempt_at = now
        document = await self._fetch_json(await self._resolve_jwks_url())
        self._cached = _CachedKeys(
            keys=signing_keys_from_jwks(document), fetched_at=now
        )
        return self._cached

    async def _resolve_jwks_url(self) -> str:
        if self._jwks_url is not None:
            return self._jwks_url
        document = await self._fetch_json(self._issuer.rstrip("/") + DISCOVERY_PATH)
        try:
            discovery = DiscoveryDocument.model_validate(document)
        except PydanticValidationError as error:
            raise self._unavailable(INVALID_DISCOVERY_DOCUMENT) from error
        # OpenID Connect Discovery §4.3: a mismatching issuer means the document is
        # not the configured provider's, and its keys must not be trusted.
        if discovery.issuer != self._issuer:
            raise self._unavailable(DISCOVERY_ISSUER_MISMATCH)
        if not is_secure_or_loopback_url(discovery.jwks_uri):
            raise self._unavailable(INSECURE_JWKS_URI)
        self._jwks_url = discovery.jwks_uri
        return discovery.jwks_uri

    # The decoded JSON is Any-shaped by nature; it is validated right after, by the
    # discovery model or by signing_keys_from_jwks.
    async def _fetch_json(self, url: str) -> object:
        try:
            response = await self._http_client.get(
                url, headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            if len(response.content) > MAX_DOCUMENT_BYTES:
                raise self._unavailable(DOCUMENT_TOO_LARGE)
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            # ValueError covers a body that is not JSON (json.JSONDecodeError).
            raise self._unavailable(type(error).__name__) from error

    @staticmethod
    def _unavailable(reason: str) -> IdentityProviderUnavailableError:
        # Fetched per call, not held in a module global: with cache_logger_on_first_use
        # a global proxy keeps the processor chain it first saw.
        structlog.get_logger(__name__).warning("jwks_fetch_failed", reason=reason)
        return IdentityProviderUnavailableError(_UNAVAILABLE_MESSAGE)
