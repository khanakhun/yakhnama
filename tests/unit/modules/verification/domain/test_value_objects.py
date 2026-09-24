"""Unit tests for ``yakhnama.modules.verification.domain.value_objects``."""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.verification.domain.value_objects import (
    TRANSITION_REASON_MAX_LENGTH,
    TargetKind,
    Transition,
    VerificationState,
    VerificationTarget,
)

IDS = SequentialIdGenerator(seed=11)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
PAKISTAN = timezone(timedelta(hours=5))


def _transition(**overrides: object) -> Transition:
    fields: dict[str, object] = {
        "from_state": VerificationState.SUBMITTED,
        "to_state": VerificationState.UNDER_REVIEW,
        "actor_id": IDS.new_id(),
        "reason": "Starting review.",
        "occurred_at": datetime(2026, 9, 23, 12, tzinfo=UTC),
        "is_human": True,
    }
    fields.update(overrides)
    return Transition.model_validate(fields)


def test_verification_target_uuid7_id_builds() -> None:
    target_id = IDS.new_id()

    target = VerificationTarget(kind=TargetKind.CLAIM, target_id=target_id)

    assert target.kind is TargetKind.CLAIM
    assert target.target_id == target_id


def test_verification_target_uuid4_id_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        VerificationTarget(kind=TargetKind.REPORT, target_id=uuid.uuid4())


def test_transition_offset_time_normalised_to_utc() -> None:
    local = datetime(2026, 9, 23, 17, tzinfo=PAKISTAN)

    transition = _transition(occurred_at=local)

    assert transition.occurred_at == datetime(2026, 9, 23, 12, tzinfo=UTC)
    assert transition.occurred_at.tzinfo is UTC


def test_transition_naive_time_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _transition(occurred_at=datetime(2026, 9, 23, 12))  # noqa: DTZ001  # reason: the naive value under test


def test_transition_reason_multiline_kept_with_lf() -> None:
    transition = _transition(reason="  First line\r\nsecond line  ")

    assert transition.reason == "First line\nsecond line"


def test_transition_reason_at_max_length_accepted() -> None:
    transition = _transition(reason="x" * TRANSITION_REASON_MAX_LENGTH)

    assert transition.reason is not None
    assert len(transition.reason) == TRANSITION_REASON_MAX_LENGTH


def test_transition_reason_over_max_length_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        _transition(reason="x" * (TRANSITION_REASON_MAX_LENGTH + 1))


@pytest.mark.parametrize(
    "reason", ["", "   ", "bad\x00byte", f"rtl{RIGHT_TO_LEFT_OVERRIDE}override"]
)
def test_transition_reason_blank_or_unsafe_rejected(reason: str) -> None:
    with pytest.raises(PydanticValidationError):
        _transition(reason=reason)


def test_transition_without_reason_builds() -> None:
    transition = _transition(reason=None, to_state=VerificationState.SUBMITTED)

    assert transition.reason is None
