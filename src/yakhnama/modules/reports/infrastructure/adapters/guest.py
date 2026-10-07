"""Crypto and randomness behind the guest submission ports (ADR 0020).

``HmacGuestChallengeSigner`` signs a challenge as the dotted token
``v1.<salt>.<difficulty_bits>.<expires_at as Unix seconds>.<signature>``, where the
signature is HMAC-SHA256 under the server's ``guest_challenge_secret`` over
everything before it, in unpadded URL-safe base64. Nothing is stored when a
challenge is issued; a forged or edited token fails the constant-time comparison.
Rotating the secret invalidates every outstanding challenge, which costs a guest at
most one more proof of work.

``SecretsGuestSecretGenerator`` draws from the operating system's CSPRNG
(``secrets``): 128-bit salts, 256-bit capabilities and receipt references of eight
characters from an alphabet without look-alike characters.

Patterns: Adapter.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Final

from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.reports.domain.guest_submissions import (
    REFERENCE_ALPHABET,
    REFERENCE_GROUP_LENGTH,
    GuestChallenge,
)

CHALLENGE_FORMAT: Final = "v1"
TOKEN_PARTS: Final = 5
SALT_BYTES: Final = 16
CAPABILITY_BYTES: Final = 32
SECRET_MIN_BYTES: Final = 32


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class HmacGuestChallengeSigner:
    """``GuestChallengeSigner`` with HMAC-SHA256 under a server secret.

    Implements: Adapter.
    """

    def __init__(self, secret: bytes) -> None:
        """Create the signer.

        Args:
            secret: The key, at least 32 bytes.

        Raises:
            ValueError: If the key is shorter than 32 bytes.
        """
        if len(secret) < SECRET_MIN_BYTES:
            message = "the guest challenge secret must be at least 32 bytes"
            raise ValueError(message)
        self._secret = secret

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._secret, payload.encode(), hashlib.sha256).digest()
        return _base64url(digest)

    def sign(self, challenge: GuestChallenge) -> str:
        """Return the signed token of ``challenge``.

        Args:
            challenge: The challenge to hand out.

        Returns:
            ``v1.<salt>.<bits>.<expiry>.<signature>``.
        """
        expires = int(challenge.expires_at.timestamp())
        payload = (
            f"{CHALLENGE_FORMAT}.{challenge.salt}.{challenge.difficulty_bits}.{expires}"
        )
        return f"{payload}.{self._signature(payload)}"

    def verify(self, token: str) -> GuestChallenge | None:
        """Return the challenge of a token this signer issued.

        Args:
            token: What the client sent back.

        Returns:
            The challenge, or ``None`` for a malformed or forged token.
        """
        parts = token.split(".")
        if len(parts) != TOKEN_PARTS or parts[0] != CHALLENGE_FORMAT:
            return None
        payload, signature = token.rsplit(".", 1)
        # Bytes, not text: compare_digest refuses non-ASCII text with a TypeError,
        # and a client may send anything.
        expected = self._signature(payload).encode()
        if not hmac.compare_digest(signature.encode(), expected):
            return None
        _, salt, bits, expires = parts[:4]
        if not all(part.isascii() and part.isdecimal() for part in (bits, expires)):
            return None
        try:
            return GuestChallenge(
                salt=salt,
                difficulty_bits=int(bits),
                expires_at=datetime.fromtimestamp(int(expires), tz=UTC),
            )
        except (PydanticValidationError, OverflowError, ValueError):
            return None


class SecretsGuestSecretGenerator:
    """``GuestSecretGenerator`` over the ``secrets`` module.

    Implements: Adapter.
    """

    def new_salt(self) -> str:
        """Return a 128-bit salt in URL-safe base64.

        Returns:
            22 characters.
        """
        return _base64url(secrets.token_bytes(SALT_BYTES))

    def new_capability(self) -> str:
        """Return a 256-bit capability in URL-safe base64.

        Returns:
            43 characters.
        """
        return _base64url(secrets.token_bytes(CAPABILITY_BYTES))

    def new_reference(self) -> str:
        """Return a reference such as ``YK-7KQM-3HXA``.

        Returns:
            ``YK-`` and two groups of four characters of ``REFERENCE_ALPHABET``.
        """
        groups = (
            "".join(
                secrets.choice(REFERENCE_ALPHABET)
                for _ in range(REFERENCE_GROUP_LENGTH)
            )
            for _ in range(2)
        )
        return "YK-" + "-".join(groups)
