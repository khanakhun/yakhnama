"""Unit tests for ``yakhnama.modules.audit.domain.value_objects``."""

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.audit.domain.value_objects import (
    AUDIT_ACTION_PATTERN,
    DIGEST_PATTERN,
    NO_STATE_DIGESTS,
    REQUEST_ID_MAX_LENGTH,
    TARGET_TYPE_MAX_LENGTH,
    AuditAction,
    AuditTarget,
    Digest,
    RequestId,
    StateDigests,
    compute_digest,
    is_digest,
)

IDS = SequentialIdGenerator()
ACTION: TypeAdapter[str] = TypeAdapter(AuditAction)
DIGEST: TypeAdapter[str] = TypeAdapter(Digest)
REQUEST_ID: TypeAdapter[str] = TypeAdapter(RequestId)


# --------------------------------------------------------------------------- #
# AuditAction                                                                 #
# --------------------------------------------------------------------------- #


@given(st.from_regex(AUDIT_ACTION_PATTERN, fullmatch=True))
def test_audit_action_matching_pattern_is_accepted(action: str) -> None:
    value = ACTION.validate_python(action)

    assert value == action
    assert 2 <= len(value) <= 64


@pytest.mark.parametrize(
    "action",
    ["a", "1reports.x", "Reports.report_submitted", "reports report", "a" * 65, ""],
)
def test_audit_action_malformed_is_rejected(action: str) -> None:
    with pytest.raises(PydanticValidationError):
        ACTION.validate_python(action)


# --------------------------------------------------------------------------- #
# AuditTarget                                                                 #
# --------------------------------------------------------------------------- #


def test_audit_target_snake_case_type_and_uuid7_is_accepted() -> None:
    target_id = IDS.new_id()

    target = AuditTarget(target_type="verification_case", target_id=target_id)

    assert target.target_id == target_id


@pytest.mark.parametrize(
    "target_type", ["", "Source", "source-type", "1source", "a" * 65]
)
def test_audit_target_malformed_type_is_rejected(target_type: str) -> None:
    with pytest.raises(PydanticValidationError):
        AuditTarget(target_type=target_type, target_id=IDS.new_id())


def test_audit_target_max_length_type_is_accepted() -> None:
    target_type = "a" * TARGET_TYPE_MAX_LENGTH

    target = AuditTarget(target_type=target_type, target_id=IDS.new_id())

    assert target.target_type == target_type


# --------------------------------------------------------------------------- #
# Digest                                                                      #
# --------------------------------------------------------------------------- #


@given(st.binary(max_size=256))
def test_compute_digest_is_prefixed_lowercase_sha256(data: bytes) -> None:
    digest = compute_digest(data)

    assert digest == "sha256:" + hashlib.sha256(data).hexdigest()
    assert is_digest(digest)
    assert DIGEST.validate_python(digest) == digest


@given(st.from_regex(DIGEST_PATTERN, fullmatch=True))
def test_digest_matching_pattern_is_accepted(digest: str) -> None:
    value = DIGEST.validate_python(digest)

    assert value == digest
    assert is_digest(digest)


@pytest.mark.parametrize(
    "digest",
    [
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "sha512:" + "a" * 64,
        "a" * 64,
        "sha256:" + "g" * 64,
        "",
    ],
)
def test_digest_malformed_is_rejected(digest: str) -> None:
    assert not is_digest(digest)
    with pytest.raises(PydanticValidationError):
        DIGEST.validate_python(digest)


@given(st.text(max_size=80))
def test_digest_validation_agrees_with_is_digest(candidate: str) -> None:
    expected = is_digest(candidate)

    try:
        DIGEST.validate_python(candidate)
    except PydanticValidationError:
        accepted = False
    else:
        accepted = True

    assert accepted == expected


# --------------------------------------------------------------------------- #
# StateDigests and RequestId                                                  #
# --------------------------------------------------------------------------- #


def test_no_state_digests_has_neither_digest() -> None:
    digests = NO_STATE_DIGESTS

    assert digests == StateDigests(before=None, after=None)


def test_state_digests_malformed_digest_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        StateDigests(before="sha256:nothex")


def test_request_id_is_stripped_and_bounded() -> None:
    value = REQUEST_ID.validate_python("  req-01J8Z  ")

    assert value == "req-01J8Z"
    with pytest.raises(PydanticValidationError):
        REQUEST_ID.validate_python("a" * (REQUEST_ID_MAX_LENGTH + 1))


@pytest.mark.parametrize(
    "request_id", ["req\n1", "req\x001", f"req{chr(0x202E)}1", " "]
)
def test_request_id_unsafe_or_empty_is_rejected(request_id: str) -> None:
    with pytest.raises(PydanticValidationError):
        REQUEST_ID.validate_python(request_id)
