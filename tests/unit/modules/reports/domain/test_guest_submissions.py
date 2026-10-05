"""Unit tests for the guest submission domain: proof of work and the aggregate."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.media.public import PUBLISHABLE_MIME_TYPES
from yakhnama.modules.reports.domain.errors import (
    GuestMediaLimitError,
    GuestSubmissionClosedError,
    GuestSubmissionLimitError,
)
from yakhnama.modules.reports.domain.guest_submissions import (
    GUEST_IMAGE_TYPES,
    GUEST_MEDIA_MAX,
    REFERENCE_ALPHABET,
    GuestChallenge,
    GuestSubmission,
    GuestSubmissionLimits,
    is_proof_of_work_valid,
    leading_zero_bits,
    sha256_hex,
)
from yakhnama.modules.reports.domain.value_objects import GuestImageType

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=901)
SUBMISSION_ID = IDS.new_id()
REPORT_ID = IDS.new_id()
CAPABILITY = "c" * 43
FINGERPRINT = "f" * 64
LIMITS = GuestSubmissionLimits()


def opened(clock: FrozenClock | None = None) -> GuestSubmission:
    """Return a submission opened at ``NOW``."""
    return GuestSubmission.open(
        SUBMISSION_ID,
        CAPABILITY,
        limits=LIMITS,
        clock=clock or FrozenClock(NOW),
    ).state


def solve(salt: str, difficulty_bits: int) -> str:
    """Return the smallest decimal nonce solving the challenge."""
    nonce = 0
    while not is_proof_of_work_valid(salt, str(nonce), difficulty_bits):
        nonce += 1
    return str(nonce)


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        (b"\x80", 0),
        (b"\x01", 7),
        (b"\x00\x40", 9),
        (b"\x00\x00", 16),
        (b"", 0),
    ],
)
def test_leading_zero_bits_counts_bits_before_the_first_one(
    digest: bytes, expected: int
) -> None:
    result = leading_zero_bits(digest)

    assert result == expected


@given(st.binary(min_size=1, max_size=32))
def test_leading_zero_bits_matches_integer_bit_length(digest: bytes) -> None:
    value = int.from_bytes(digest, "big")

    result = leading_zero_bits(digest)

    assert result == len(digest) * 8 - value.bit_length()


def test_proof_of_work_solution_found_by_search_is_accepted() -> None:
    nonce = solve("salt-for-test-0000000", 8)

    is_valid = is_proof_of_work_valid("salt-for-test-0000000", nonce, 8)

    digest = hashlib.sha256(f"salt-for-test-0000000{nonce}".encode()).digest()
    assert is_valid is True
    assert leading_zero_bits(digest) >= 8


def test_proof_of_work_nonce_with_too_few_zero_bits_is_refused() -> None:
    salt = "salt-for-test-0000000"
    nonce = next(
        str(candidate)
        for candidate in range(100)
        if leading_zero_bits(hashlib.sha256(f"{salt}{candidate}".encode()).digest())
        < 12
    )

    is_valid = is_proof_of_work_valid(salt, nonce, 12)

    assert is_valid is False


def test_guest_challenge_expiry_and_solution() -> None:
    challenge = GuestChallenge(
        salt="salt-for-test-0000000",
        difficulty_bits=4,
        expires_at=NOW + timedelta(minutes=10),
    )

    nonce = solve(challenge.salt, 4)

    assert challenge.is_solved_by(nonce) is True
    assert challenge.is_expired(NOW) is False
    assert challenge.is_expired(NOW + timedelta(minutes=10)) is True


@pytest.mark.parametrize(
    "fields",
    [
        {"salt": "short", "difficulty_bits": 8},
        {"salt": "has.a.dot.in.the.salt0", "difficulty_bits": 8},
        {"salt": "salt-for-test-0000000", "difficulty_bits": 0},
        {"salt": "salt-for-test-0000000", "difficulty_bits": 33},
    ],
)
def test_guest_challenge_invalid_fields_are_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GuestChallenge.model_validate({**fields, "expires_at": NOW})


def test_guest_submission_limits_non_positive_lifetime_is_refused() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        GuestSubmissionLimits(capability_ttl=timedelta(0))


def test_guest_image_types_equal_the_media_publishable_types() -> None:
    publishable = {mime_type.value for mime_type in PUBLISHABLE_MIME_TYPES}

    assert set(GUEST_IMAGE_TYPES) == publishable
    assert set(get_args(GuestImageType)) == publishable


def test_reference_alphabet_has_no_look_alike_characters() -> None:
    assert not set("01ILOU") & set(REFERENCE_ALPHABET)
    assert len(set(REFERENCE_ALPHABET)) == len(REFERENCE_ALPHABET) == 30


def test_open_submission_stores_only_the_capability_digest() -> None:
    submission = opened()

    assert submission.capability_digest == sha256_hex(CAPABILITY)
    assert CAPABILITY not in submission.model_dump_json()
    assert submission.expires_at == NOW + LIMITS.capability_ttl
    assert submission.is_open is True
    assert submission.version == 1


def test_is_capability_accepts_only_the_issued_capability() -> None:
    submission = opened()

    assert submission.is_capability(CAPABILITY) is True
    assert submission.is_capability("d" * 43) is False


def test_is_expired_from_expires_at_on() -> None:
    submission = opened()

    assert submission.is_expired(submission.expires_at - timedelta(seconds=1)) is False
    assert submission.is_expired(submission.expires_at) is True


def test_attach_media_adds_up_to_the_limit_then_refuses() -> None:
    clock = FrozenClock(NOW)
    submission = opened(clock)
    for _ in range(GUEST_MEDIA_MAX):
        submission = submission.attach_media(IDS.new_id(), clock=clock).state

    with pytest.raises(GuestMediaLimitError):
        submission.attach_media(IDS.new_id(), clock=clock)

    assert len(submission.media_ids) == GUEST_MEDIA_MAX
    assert submission.version == 1 + GUEST_MEDIA_MAX


def test_attach_media_same_asset_twice_changes_nothing() -> None:
    clock = FrozenClock(NOW)
    asset_id = IDS.new_id()
    submission = opened(clock).attach_media(asset_id, clock=clock).state

    again = submission.attach_media(asset_id, clock=clock).state

    assert again == submission


def test_record_report_closes_the_submission() -> None:
    clock = FrozenClock(NOW)

    closed = (
        opened(clock)
        .record_report(REPORT_ID, "YK-ABCD-2345", FINGERPRINT, clock=clock)
        .state
    )

    assert closed.is_open is False
    assert closed.report_id == REPORT_ID
    assert closed.reference == "YK-ABCD-2345"
    assert closed.submitted_at == NOW


def test_closed_submission_refuses_media_and_a_second_report() -> None:
    clock = FrozenClock(NOW)
    closed = (
        opened(clock)
        .record_report(REPORT_ID, "YK-ABCD-2345", FINGERPRINT, clock=clock)
        .state
    )

    with pytest.raises(GuestSubmissionClosedError):
        closed.attach_media(IDS.new_id(), clock=clock)
    with pytest.raises(GuestSubmissionClosedError):
        closed.record_report(REPORT_ID, "YK-ABCD-2346", FINGERPRINT, clock=clock)


@pytest.mark.parametrize(
    "overrides",
    [
        {"report_id": REPORT_ID},
        {"reference": "YK-ABCD-2345"},
        {"reference": "YK-ABCD-0000"},
        {"media_ids": (SUBMISSION_ID, SUBMISSION_ID)},
        {"expires_at": NOW},
        {"updated_at": NOW - timedelta(seconds=1)},
    ],
)
def test_guest_submission_inconsistent_state_is_refused(
    overrides: dict[str, object],
) -> None:
    fields = opened().model_dump()

    with pytest.raises(ValidationError):
        GuestSubmission.model_validate({**fields, **overrides})


def test_guest_submission_limit_error_carries_retry_after() -> None:
    error = GuestSubmissionLimitError.retry_after(0)

    assert error.retry_after_seconds == 1
    assert error.details["retry_after_seconds"] == 1
