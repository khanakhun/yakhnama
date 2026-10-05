"""Unit tests for the guest challenge signer and secret generator (no I/O)."""

import base64
import hashlib
import hmac
import re
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from yakhnama.modules.reports.domain.guest_submissions import (
    CAPABILITY_PATTERN,
    REFERENCE_PATTERN,
    SALT_PATTERN,
    GuestChallenge,
)
from yakhnama.modules.reports.infrastructure.adapters.guest import (
    HmacGuestChallengeSigner,
    SecretsGuestSecretGenerator,
)

SECRET = b"unit-test-guest-challenge-key-000000000000"
CHALLENGE = GuestChallenge(
    salt="AbCdEfGhIjKlMnOpQrStUv",
    difficulty_bits=16,
    expires_at=datetime(2026, 10, 5, 12, 10, tzinfo=UTC),
)


def test_signer_round_trips_a_challenge() -> None:
    signer = HmacGuestChallengeSigner(SECRET)

    token = signer.sign(CHALLENGE)

    assert signer.verify(token) == CHALLENGE
    assert len(token) <= 512
    assert token.startswith("v1.AbCdEfGhIjKlMnOpQrStUv.16.")


def test_signer_refuses_a_token_signed_with_another_key() -> None:
    token = HmacGuestChallengeSigner(b"another-key-for-the-guest-challenges-0000").sign(
        CHALLENGE
    )

    result = HmacGuestChallengeSigner(SECRET).verify(token)

    assert result is None


@pytest.mark.parametrize(
    "edit",
    [
        lambda token: token.replace(".16.", ".1.", 1),
        lambda token: token.replace("AbCd", "ZbCd", 1),
        lambda token: token[:-2],
        lambda token: "v2" + token[2:],
        lambda token: token + ".extra",
        lambda token: "garbage",
        lambda token: token[:-1] + "\u00e9",
        lambda token: token.replace(".16.", ".\u0661\u0666.", 1),
        lambda token: "",
    ],
)
def test_signer_refuses_edited_or_malformed_tokens(
    edit: Callable[[str], str],
) -> None:
    signer = HmacGuestChallengeSigner(SECRET)
    token = signer.sign(CHALLENGE)

    result = signer.verify(edit(token))

    assert result is None


def _signed(payload: str) -> str:
    digest = hmac.new(SECRET, payload.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"{payload}.{signature}"


def test_signer_refuses_validly_signed_but_invalid_fields() -> None:
    signer = HmacGuestChallengeSigner(SECRET)
    payloads = [
        "v1.AbCdEfGhIjKlMnOpQrStUv.99.1791201000",
        "v1.AbCdEfGhIjKlMnOpQrStUv.x6.1791201000",
        "v1.short.16.1791201000",
        "v1.AbCdEfGhIjKlMnOpQrStUv.16.99999999999999999999",
    ]

    results = [signer.verify(_signed(payload)) for payload in payloads]

    assert results == [None, None, None, None]


def test_signer_short_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        HmacGuestChallengeSigner(b"too-short")


def test_secret_generator_values_match_their_patterns_and_differ() -> None:
    secrets = SecretsGuestSecretGenerator()

    salts = {secrets.new_salt() for _ in range(20)}
    capabilities = {secrets.new_capability() for _ in range(20)}
    references = [secrets.new_reference() for _ in range(20)]

    assert len(salts) == 20
    assert len(capabilities) == 20
    assert all(re.fullmatch(SALT_PATTERN, salt) for salt in salts)
    assert all(re.fullmatch(CAPABILITY_PATTERN, value) for value in capabilities)
    assert all(re.fullmatch(REFERENCE_PATTERN, value) for value in references)
