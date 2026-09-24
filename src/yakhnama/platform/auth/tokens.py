"""Validation of bearer JWT access tokens with PyJWT (ADR 0015).

``TokenValidator.validate`` accepts a token only if all of these hold:

- the header's ``alg`` is in ``oidc_allowed_algorithms`` (so ``none`` and every
  ``HS*`` algorithm are refused before any key is looked up) and equals the
  algorithm bound to the signing key;
- the header's ``typ``, when present, is one of ``oidc_accepted_token_types``
  (``JWT`` and ``at+jwt`` by default), compared case-insensitively and with an
  optional ``application/`` prefix (RFC 7515 §4.1.9), so a token of another type
  that the provider signs with the same keys, such as an ID token typed ``ID``, is
  refused;
- the header names a ``kid`` the ``JwksClient`` knows, after at most one refetch;
- the signature verifies;
- ``iss`` equals the configured issuer and ``aud`` contains the configured audience;
- ``exp``, ``iat`` and ``sub`` are present, ``exp`` is in the future, ``iat`` is not
  in the future and ``nbf``, when present, is in the past, each with
  ``oidc_leeway_seconds`` of tolerance.

PyJWT checks the signature, ``iss``, ``aud`` and the presence of the required claims.
The time claims are checked here against the injected ``Clock`` instead of PyJWT's
wall clock, so the rule "time comes from an injected clock" (``AGENTS.md`` §4.2) holds
and expiry is tested with a frozen clock rather than by sleeping.

Every failure becomes the same ``AuthenticationError`` with a fixed message. The
token, its claims and PyJWT's error text never reach the error or a log line; only
the PyJWT error type is logged, as ``reason``.

Patterns: Adapter, Anti-Corruption Layer (claims to ``Principal``).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

import jwt
import structlog
from pydantic import BaseModel, ConfigDict, Field

from yakhnama.platform.auth.jwks import JwksClient
from yakhnama.platform.auth.principal import (
    DISPLAY_NAME_MAX_LENGTH,
    ISSUER_MAX_LENGTH,
    MAX_ROLES,
    SUBJECT_MAX_LENGTH,
    TOKEN_ID_MAX_LENGTH,
    Principal,
    RoleName,
)
from yakhnama.platform.settings import DEFAULT_ACCEPTED_TOKEN_TYPES
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import AuthenticationError

REJECTION_MESSAGE: Final = "The bearer token is missing or invalid."
# Real access tokens are 1-3 KB; the cap bounds the work done on hostile input
# before the signature is even checked.
MAX_TOKEN_LENGTH: Final = 8192
KEY_ID_MAX_LENGTH: Final = 256
REQUIRED_CLAIMS: Final = ("exp", "iat", "iss", "aud", "sub")
# Logged reasons that are not PyJWT error types.
OVERSIZED_CREDENTIAL: Final = "oversized_credential"
EXPIRED: Final = "expired"
NOT_YET_VALID: Final = "not_yet_valid"
ISSUED_IN_FUTURE: Final = "issued_in_future"
ALGORITHM_NOT_ALLOWED: Final = "algorithm_not_allowed"
TYPE_NOT_ACCEPTED: Final = "token_type_not_accepted"
MISSING_KEY_ID: Final = "missing_key_id"
UNKNOWN_KEY_ID: Final = "unknown_key_id"
INVALID_CLAIMS: Final = "invalid_claims"
DEFAULT_ROLES_CLAIM: Final = "realm_access.roles"
_MEDIA_TYPE_PREFIX: Final = "application/"


def normalise_token_type(token_type: str) -> str:
    """Return the comparable form of a JOSE ``typ`` value.

    RFC 7515 §4.1.9: media type names compare case-insensitively and a value
    without ``/`` means the same as with the ``application/`` prefix.

    Args:
        token_type: A ``typ`` header value or a configured accepted type.

    Returns:
        The value in lower case without the ``application/`` prefix.
    """
    lowered = token_type.lower()
    return lowered.removeprefix(_MEDIA_TYPE_PREFIX)


def claim_at(claims: Mapping[str, object], path: str) -> object:
    """Return the value at a dotted claim path, or ``None`` if any part is missing.

    Args:
        claims: The decoded token payload.
        path: For example ``"realm_access.roles"``.

    Returns:
        The value, or ``None``.
    """
    value: object = claims
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


class AccessTokenClaims(BaseModel):
    """The claims of a verified access token that the API maps to a ``Principal``.

    PyJWT has already checked ``exp``, ``nbf``, ``iat``, ``iss`` and ``aud``; this
    model checks the shape and bounds of the values the API keeps.

    Implements: Anti-Corruption Layer (OIDC access-token claims).

    Attributes:
        sub: Subject.
        iss: Issuer.
        exp: Expiry, seconds since the epoch.
        iat: Issue time, seconds since the epoch.
        nbf: Not-before time, seconds since the epoch, optional.
        jti: Token id, optional.
        preferred_username: Login name, optional personal data; the only source
            of ``display_name``.
        realm_roles: The roles read from the configured claim path
            (``oidc_roles_claim``); always set by ``TokenValidator``, never read
            from a claim of this name.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    sub: str = Field(min_length=1, max_length=SUBJECT_MAX_LENGTH)
    iss: str = Field(min_length=1, max_length=ISSUER_MAX_LENGTH)
    exp: float = Field(ge=0, allow_inf_nan=False)
    iat: float = Field(ge=0, allow_inf_nan=False)
    nbf: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    jti: str | None = Field(default=None, min_length=1, max_length=TOKEN_ID_MAX_LENGTH)
    preferred_username: str | None = Field(
        default=None, max_length=DISPLAY_NAME_MAX_LENGTH
    )
    realm_roles: tuple[RoleName, ...] = Field(default=(), max_length=MAX_ROLES)

    def time_problem(self, now: float, leeway: float) -> str | None:
        """Return why the token is not valid at ``now``, or ``None`` if it is.

        Args:
            now: The current instant, seconds since the epoch.
            leeway: Tolerated clock skew in seconds.

        Returns:
            ``"expired"``, ``"not_yet_valid"``, ``"issued_in_future"`` or ``None``.
        """
        if self.exp <= now - leeway:
            return EXPIRED
        if self.nbf is not None and self.nbf > now + leeway:
            return NOT_YET_VALID
        if self.iat > now + leeway:
            return ISSUED_IN_FUTURE
        return None

    def to_principal(self) -> Principal:
        """Map the claims to the API's ``Principal``.

        ``display_name`` comes from ``preferred_username`` only, never from
        ``name``: a login name is chosen by the user for display, while ``name``
        is typically the legal full name, which the API has no need to hold
        (security review, Phase 2).

        Returns:
            The principal.
        """
        username = (self.preferred_username or "").strip()
        return Principal(
            subject=self.sub,
            issuer=self.iss,
            realm_roles=frozenset(self.realm_roles),
            display_name=username or None,
            token_id=self.jti,
            expires_at=datetime.fromtimestamp(self.exp, UTC),
        )


class TokenValidator:
    """Checks bearer JWTs against the provider's keys and configured claims.

    Implements: Adapter, Anti-Corruption Layer (claims to ``Principal``).
    """

    def __init__(  # noqa: PLR0913  # reason: keyword-only wiring from settings
        self,
        *,
        jwks_client: JwksClient,
        issuer: str,
        audience: str,
        algorithms: Sequence[str],
        leeway_seconds: int,
        clock: Clock,
        roles_claim: str = DEFAULT_ROLES_CLAIM,
        accepted_token_types: Sequence[str] = DEFAULT_ACCEPTED_TOKEN_TYPES,
    ) -> None:
        """Create the validator.

        Args:
            jwks_client: Source of the signing keys.
            issuer: The exact ``iss`` every token must carry.
            audience: The value ``aud`` must contain.
            algorithms: The allow-list of JWS algorithms.
            leeway_seconds: Clock-skew tolerance for the time claims.
            clock: Source of the current instant for the time claims.
            roles_claim: The one dotted claim path roles are read from; no other
                claim grants a role.
            accepted_token_types: The ``typ`` header values accepted when the
                header carries one; a token without ``typ`` is not refused for it.
        """
        self._jwks_client = jwks_client
        self._issuer = issuer
        self._audience = audience
        self._algorithms = tuple(algorithms)
        self._leeway_seconds = leeway_seconds
        self._clock = clock
        self._roles_claim = roles_claim
        self._accepted_token_types = frozenset(
            normalise_token_type(token_type) for token_type in accepted_token_types
        )

    async def validate(self, token: str) -> Principal:
        """Verify ``token`` and return the principal it authenticates.

        Args:
            token: The compact JWS from the ``Authorization: Bearer`` header.

        Returns:
            The authenticated principal.

        Raises:
            AuthenticationError: If the token is rejected for any reason.
            IdentityProviderUnavailableError: If the signing keys cannot be fetched.
        """
        if len(token) > MAX_TOKEN_LENGTH:
            raise _rejected(OVERSIZED_CREDENTIAL)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise _rejected(type(error).__name__) from None
        if header.get("alg") not in self._algorithms:
            raise _rejected(ALGORITHM_NOT_ALLOWED)
        if not self._is_accepted_type(header):
            raise _rejected(TYPE_NOT_ACCEPTED)
        key_id = header.get("kid")
        if not isinstance(key_id, str) or not 0 < len(key_id) <= KEY_ID_MAX_LENGTH:
            raise _rejected(MISSING_KEY_ID)
        signing_key = await self._jwks_client.get_signing_key(key_id)
        if signing_key is None:
            raise _rejected(UNKNOWN_KEY_ID)
        try:
            payload = jwt.decode(
                token,
                key=signing_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": list(REQUIRED_CLAIMS),
                    # Checked below against the injected clock.
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as error:
            # "from None": the PyJWT error text can quote claim values.
            raise _rejected(type(error).__name__) from None
        try:
            # The roles field is always overwritten from the configured path, so a
            # claim that happens to be named "realm_roles" grants nothing.
            roles = claim_at(payload, self._roles_claim)
            claims = AccessTokenClaims.model_validate(
                {**payload, "realm_roles": [] if roles is None else roles}
            )
            principal = claims.to_principal()
        # ValueError includes Pydantic's ValidationError (a claim out of bounds, too
        # many roles in total); OverflowError and OSError come from an ``exp`` too
        # far in the future for ``datetime``.
        except (ValueError, OverflowError, OSError):
            raise _rejected(INVALID_CLAIMS) from None
        problem = claims.time_problem(
            self._clock.now().timestamp(), float(self._leeway_seconds)
        )
        if problem is not None:
            raise _rejected(problem)
        return principal

    def _is_accepted_type(self, header: Mapping[str, object]) -> bool:
        # "typ" is optional in RFC 7519; many providers omit it, so its absence is
        # not a reason to refuse. A present value that is not a string is malformed.
        if "typ" not in header:
            return True
        token_type = header["typ"]
        return (
            isinstance(token_type, str)
            and normalise_token_type(token_type) in self._accepted_token_types
        )


def _rejected(reason: str) -> AuthenticationError:
    # Fetched per call, not held in a module global: with cache_logger_on_first_use
    # a global proxy keeps the processor chain it first saw.
    structlog.get_logger(__name__).info("bearer_token_rejected", reason=reason)
    return AuthenticationError(REJECTION_MESSAGE)
