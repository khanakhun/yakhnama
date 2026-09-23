"""The authenticated caller as the rest of the code base sees it.

A ``Principal`` is built only by ``TokenValidator`` from a verified access token.
Routes pass it to the identity module (``EnsureUserFromPrincipal``), which turns it
into a domain ``Actor``; no other code reads token claims.

Patterns: DTO.
"""

import hashlib
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Bounds on untrusted claim values: generous for any real provider, small enough that
# a hostile token cannot make every request carry megabytes of "roles".
SUBJECT_MAX_LENGTH = 255
ISSUER_MAX_LENGTH = 2048
ROLE_MAX_LENGTH = 100
MAX_ROLES = 100
DISPLAY_NAME_MAX_LENGTH = 200
TOKEN_ID_MAX_LENGTH = 255

RoleName = Annotated[str, Field(min_length=1, max_length=ROLE_MAX_LENGTH)]


class Principal(BaseModel):
    """A caller whose bearer token was verified.

    Implements: DTO.

    Attributes:
        subject: The token's ``sub``, stable for the user within one issuer.
        issuer: The token's ``iss``; ``(issuer, subject)`` identifies the user.
        realm_roles: Role names granted by the identity provider, read from the
            one claim path ``oidc_roles_claim`` (Keycloak: ``realm_access.roles``).
            They are the provider's names, not yet the identity module's ``Role``
            values.
        display_name: The ``preferred_username`` claim only, never ``name`` (the
            legal full name). Optional personal data: never logged; the identity
            module mirrors new users without it.
        token_id: The token's ``jti``, when the provider sets one.
        expires_at: When the token expires (UTC).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=SUBJECT_MAX_LENGTH)
    issuer: str = Field(min_length=1, max_length=ISSUER_MAX_LENGTH)
    realm_roles: frozenset[RoleName] = Field(
        default_factory=frozenset, max_length=MAX_ROLES
    )
    display_name: str | None = Field(
        default=None, min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH
    )
    token_id: str | None = Field(
        default=None, min_length=1, max_length=TOKEN_ID_MAX_LENGTH
    )
    expires_at: AwareDatetime

    def scope_key(self) -> str:
        """Return a stable, opaque key for this principal.

        Rate limiting and idempotency key their state by it. It is a SHA-256 hash of
        the issuer and subject, so the stored key reveals neither, and two issuers
        that reuse a subject never share state.

        Returns:
            64 lowercase hexadecimal characters.
        """
        # "\n" cannot appear in a URL issuer, so the concatenation is unambiguous.
        material = f"{self.issuer}\n{self.subject}".encode()
        return hashlib.sha256(material).hexdigest()
