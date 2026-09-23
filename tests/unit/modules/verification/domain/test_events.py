"""Unit tests for ``yakhnama.modules.verification.domain.events``."""

import pytest

from yakhnama.modules.verification.domain.events import (
    VerificationAssigned,
    VerificationCaseEvent,
    VerificationCaseOpened,
    VerificationTransitioned,
)


@pytest.mark.parametrize(
    ("event_class", "event_type"),
    [
        (VerificationCaseOpened, "verification.verification_case_opened"),
        (VerificationTransitioned, "verification.verification_transitioned"),
        (VerificationAssigned, "verification.verification_assigned"),
    ],
)
def test_verification_event_classes_declare_stable_event_type(
    event_class: type[VerificationCaseEvent], event_type: str
) -> None:
    declared = event_class.event_type

    assert declared == event_type


def test_verification_transitioned_has_no_reason_field() -> None:
    fields = set(VerificationTransitioned.model_fields)

    assert "reason" not in fields
    assert "has_reason" in fields
