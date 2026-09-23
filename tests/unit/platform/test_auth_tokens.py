"""Unit tests for ``TokenValidator`` and the claim mapping to ``Principal``."""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from structlog.testing import capture_logs

from tests.fakes.auth import (
    TEST_AUDIENCE,
    TEST_ISSUER,
    FakeJwksClient,
    TestKeyPair,
    access_token_claims,
    issue_token,
    session_key_pair,
)
from tests.fakes.clock import FrozenClock
from yakhnama.platform.auth.principal import MAX_ROLES, Principal
from yakhnama.platform.auth.tokens import (
    MAX_TOKEN_LENGTH,
    REJECTION_MESSAGE,
    AccessTokenClaims,
    TokenValidator,
)
from yakhnama.shared_kernel.errors import AuthenticationError

LEEWAY_SECONDS = 10
EXPECTED_TOKEN_ID = "jti-" + "user-1"


def _validator(
    clock: FrozenClock,
    jwks: FakeJwksClient | None = None,
    algorithms: tuple[str, ...] = ("RS256", "ES256"),
) -> TokenValidator:
    return TokenValidator(
        jwks_client=jwks if jwks is not None else FakeJwksClient([session_key_pair()]),
        issuer=TEST_ISSUER,
        audience=TEST_AUDIENCE,
        algorithms=algorithms,
        leeway_seconds=LEEWAY_SECONDS,
        clock=clock,
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unsigned_token(header: dict[str, object], claims: dict[str, object]) -> str:
    return ".".join(
        (
            _b64(json.dumps(header).encode()),
            _b64(json.dumps(claims).encode()),
            "",
        )
    )


async def _rejection_reason(validator: TokenValidator, token: str) -> str:
    with capture_logs() as logs, pytest.raises(AuthenticationError) as caught:
        await validator.validate(token)
    assert caught.value.message == REJECTION_MESSAGE
    if token:
        assert token not in str(caught.value)
        assert all(token not in json.dumps(log, default=str) for log in logs)
    rejected = [log for log in logs if log["event"] == "bearer_token_rejected"]
    assert len(rejected) == 1
    return str(rejected[0]["reason"])


async def test_validate_valid_token_returns_principal(clock: FrozenClock) -> None:
    claims = access_token_claims(
        subject="user-1", realm_roles=["moderator"], name="  Amina  "
    )
    claims["roles"] = ["org_admin"]
    token = issue_token(claims, session_key_pair())

    principal = await _validator(clock).validate(token)

    assert principal == Principal(
        subject="user-1",
        issuer=TEST_ISSUER,
        realm_roles=frozenset({"moderator", "org_admin"}),
        display_name="Amina",
        token_id=EXPECTED_TOKEN_ID,
        expires_at=clock.now() + timedelta(minutes=5),
    )


async def test_validate_es256_token_with_ec_key_returns_principal(
    clock: FrozenClock,
) -> None:
    ec_pair = TestKeyPair(key_id="ec-key", algorithm="ES256")
    token = issue_token(access_token_claims(), ec_pair)

    principal = await _validator(clock, FakeJwksClient([ec_pair])).validate(token)

    assert principal.subject == "test-subject"


async def test_validate_audience_list_containing_audience_is_accepted(
    clock: FrozenClock,
) -> None:
    token = issue_token(
        access_token_claims(audience=["account", TEST_AUDIENCE]), session_key_pair()
    )

    principal = await _validator(clock).validate(token)

    assert principal.subject == "test-subject"


async def test_validate_preferred_username_used_when_name_absent(
    clock: FrozenClock,
) -> None:
    claims = access_token_claims(overrides={"preferred_username": "amina", "name": " "})
    token = issue_token(claims, session_key_pair())

    principal = await _validator(clock).validate(token)

    assert principal.display_name == "amina"


async def test_validate_without_name_claims_has_no_display_name(
    clock: FrozenClock,
) -> None:
    claims = access_token_claims(overrides={"realm_access": None, "jti": None})
    token = issue_token(claims, session_key_pair())

    principal = await _validator(clock).validate(token)

    assert principal.display_name is None
    assert principal.realm_roles == frozenset()
    assert principal.token_id is None


async def test_validate_expired_token_is_rejected(clock: FrozenClock) -> None:
    token = issue_token(access_token_claims(), session_key_pair())
    clock.advance(timedelta(minutes=5, seconds=LEEWAY_SECONDS))

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "expired"


async def test_validate_expired_within_leeway_is_accepted(clock: FrozenClock) -> None:
    token = issue_token(access_token_claims(), session_key_pair())
    clock.advance(timedelta(minutes=5, seconds=LEEWAY_SECONDS - 1))

    principal = await _validator(clock).validate(token)

    assert principal.subject == "test-subject"


async def test_validate_nbf_in_future_is_rejected(clock: FrozenClock) -> None:
    not_before = int((clock.now() + timedelta(seconds=LEEWAY_SECONDS + 1)).timestamp())
    token = issue_token(
        access_token_claims(overrides={"nbf": not_before}), session_key_pair()
    )

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "not_yet_valid"


async def test_validate_nbf_in_past_is_accepted(clock: FrozenClock) -> None:
    not_before = int((clock.now() - timedelta(seconds=1)).timestamp())
    token = issue_token(
        access_token_claims(overrides={"nbf": not_before}), session_key_pair()
    )

    principal = await _validator(clock).validate(token)

    assert principal.subject == "test-subject"


async def test_validate_iat_in_future_is_rejected(clock: FrozenClock) -> None:
    token = issue_token(
        access_token_claims(now=clock.now() + timedelta(minutes=1)), session_key_pair()
    )

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "issued_in_future"


async def test_validate_wrong_audience_is_rejected(clock: FrozenClock) -> None:
    token = issue_token(access_token_claims(audience="account"), session_key_pair())

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "InvalidAudienceError"


async def test_validate_wrong_issuer_is_rejected(clock: FrozenClock) -> None:
    token = issue_token(
        access_token_claims(issuer="https://evil.test/realms/yakhnama"),
        session_key_pair(),
    )

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "InvalidIssuerError"


@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "aud", "iss"])
async def test_validate_missing_required_claim_is_rejected(
    clock: FrozenClock, claim: str
) -> None:
    token = issue_token(
        access_token_claims(overrides={claim: None}), session_key_pair()
    )

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "MissingRequiredClaimError"


async def test_validate_alg_none_is_rejected_before_key_lookup(
    clock: FrozenClock,
) -> None:
    jwks = FakeJwksClient([session_key_pair()])
    token = _unsigned_token(
        {"alg": "none", "kid": session_key_pair().key_id}, access_token_claims()
    )

    reason = await _rejection_reason(_validator(clock, jwks), token)

    assert reason == "algorithm_not_allowed"
    assert jwks.requested_key_ids == []


async def test_validate_hs256_token_signed_with_public_key_is_rejected(
    clock: FrozenClock,
) -> None:
    # The classic confusion attack: HMAC keyed with the public key's text.
    public_pem = (
        session_key_pair()
        .private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    header = {"alg": "HS256", "kid": session_key_pair().key_id}
    signing_input = (
        _b64(json.dumps(header).encode())
        + "."
        + _b64(json.dumps(access_token_claims()).encode())
    )
    signature = jwt.algorithms.HMACAlgorithm(jwt.algorithms.HMACAlgorithm.SHA256).sign(
        signing_input.encode(), public_pem
    )
    token = signing_input + "." + _b64(signature)

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "algorithm_not_allowed"


async def test_validate_allowed_alg_not_matching_key_is_rejected(
    clock: FrozenClock,
) -> None:
    ec_pair = TestKeyPair(key_id=session_key_pair().key_id, algorithm="ES256")
    token = issue_token(access_token_claims(), ec_pair)

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "InvalidAlgorithmError"


async def test_validate_alg_outside_configured_list_is_rejected(
    clock: FrozenClock,
) -> None:
    ec_pair = TestKeyPair(key_id="ec-key", algorithm="ES256")
    token = issue_token(access_token_claims(), ec_pair)
    validator = _validator(clock, FakeJwksClient([ec_pair]), algorithms=("RS256",))

    reason = await _rejection_reason(validator, token)

    assert reason == "algorithm_not_allowed"


async def test_validate_signature_by_other_key_is_rejected(clock: FrozenClock) -> None:
    impostor = TestKeyPair(key_id=session_key_pair().key_id)
    token = issue_token(access_token_claims(), impostor)

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "InvalidSignatureError"


async def test_validate_unknown_kid_is_rejected_after_asking_jwks(
    clock: FrozenClock,
) -> None:
    jwks = FakeJwksClient([session_key_pair()])
    token = issue_token(access_token_claims(), session_key_pair(), kid="unknown")

    reason = await _rejection_reason(_validator(clock, jwks), token)

    assert reason == "unknown_key_id"
    assert jwks.requested_key_ids == ["unknown"]


async def test_validate_unknown_kid_then_rotation_is_accepted(
    clock: FrozenClock,
) -> None:
    rotated = TestKeyPair(key_id="rotated")
    jwks = FakeJwksClient([session_key_pair()])
    validator = _validator(clock, jwks)
    token = issue_token(access_token_claims(), rotated)
    await _rejection_reason(validator, token)
    jwks.publish(rotated)

    principal = await validator.validate(token)

    assert principal.subject == "test-subject"


@pytest.mark.parametrize("kid", [None, "", "k" * 257])
async def test_validate_missing_or_invalid_kid_is_rejected(
    clock: FrozenClock, kid: str | None
) -> None:
    header: dict[str, object] = {"alg": "RS256"}
    if kid is not None:
        header["kid"] = kid
    token = _unsigned_token(header, access_token_claims())

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "missing_key_id"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", "", "...."])
async def test_validate_malformed_token_is_rejected(
    clock: FrozenClock, token: str
) -> None:
    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "DecodeError"


async def test_validate_oversized_token_is_rejected_without_parsing(
    clock: FrozenClock,
) -> None:
    token = "a" * (MAX_TOKEN_LENGTH + 1)

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "oversized_credential"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": ""},
        {"sub": "s" * 256},
        {"roles": ["r"] * (MAX_ROLES + 1)},
        {"realm_access": {"roles": [""]}},
        {"exp": 1e300},
    ],
)
async def test_validate_claims_out_of_bounds_are_rejected(
    clock: FrozenClock, overrides: dict[str, object]
) -> None:
    token = issue_token(access_token_claims(overrides=overrides), session_key_pair())

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "invalid_claims"


async def test_validate_too_many_roles_in_total_is_rejected(
    clock: FrozenClock,
) -> None:
    roles = [f"role-{index}" for index in range(MAX_ROLES)]
    claims = access_token_claims(realm_roles=roles)
    claims["roles"] = ["one-more"]
    token = issue_token(claims, session_key_pair())

    reason = await _rejection_reason(_validator(clock), token)

    assert reason == "invalid_claims"


def test_access_token_claims_time_problem_is_none_inside_window() -> None:
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC).timestamp()
    claims = AccessTokenClaims(sub="s", iss=TEST_ISSUER, exp=now + 60, iat=now)

    problem = claims.time_problem(now, 0.0)

    assert problem is None
