"""Unit tests for ``yakhnama.modules.verification.domain.factories``."""

import pytest

from tests.factories.verification import (
    VerificationCaseTestFactory,
    VerificationTargetTestFactory,
)
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.unit.modules.verification.domain.walks import OPENED_AT, REVIEWER_ID
from yakhnama.modules.verification.domain.errors import CaseAlreadyOpenError
from yakhnama.modules.verification.domain.events import VerificationCaseOpened
from yakhnama.modules.verification.domain.factories import (
    VerificationCaseFactory,
    initial_state_for,
)
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (TargetKind.REPORT, VerificationState.SUBMITTED),
        (TargetKind.EVENT, VerificationState.DRAFT),
        (TargetKind.CLAIM, VerificationState.SUBMITTED),
    ],
)
def test_initial_state_for_each_kind_returns_proposed_state(
    kind: TargetKind, expected: VerificationState
) -> None:
    state = initial_state_for(kind)

    assert state is expected


@pytest.mark.parametrize("kind", list(TargetKind))
def test_open_new_target_builds_case_in_initial_state(kind: TargetKind) -> None:
    target = VerificationTargetTestFactory.build(kind=kind)

    change = VerificationCaseFactory.open(
        target,
        opened_by=REVIEWER_ID,
        clock=FrozenClock(OPENED_AT),
        ids=SequentialIdGenerator(seed=4),
    )

    case = change.state
    assert case.target == target
    assert case.initial_state is initial_state_for(kind)
    assert case.state is case.initial_state
    assert case.history == ()
    assert case.assigned_to is None
    assert case.opened_by == REVIEWER_ID
    assert case.version == 1
    assert case.created_at == case.updated_at == OPENED_AT


def test_open_new_target_emits_case_opened_event() -> None:
    target = VerificationTargetTestFactory.build(kind=TargetKind.EVENT)

    change = VerificationCaseFactory.open(
        target,
        opened_by=REVIEWER_ID,
        clock=FrozenClock(OPENED_AT),
        ids=SequentialIdGenerator(seed=4),
    )

    (event,) = change.events
    assert isinstance(event, VerificationCaseOpened)
    assert event.event_type == "verification.verification_case_opened"
    assert event.aggregate_id == change.state.id
    assert event.aggregate_type == "verification_case"
    assert event.target_kind is TargetKind.EVENT
    assert event.target_id == target.target_id
    assert event.initial_state is VerificationState.DRAFT
    assert event.opened_by == REVIEWER_ID
    assert event.occurred_at == OPENED_AT


def test_open_target_with_existing_case_raises_case_already_open() -> None:
    existing = VerificationCaseTestFactory.build()

    with pytest.raises(CaseAlreadyOpenError) as raised:
        VerificationCaseFactory.open(
            existing.target,
            opened_by=REVIEWER_ID,
            clock=FrozenClock(OPENED_AT),
            ids=SequentialIdGenerator(),
            existing_case=existing,
        )

    assert raised.value.case_id == existing.id
    assert raised.value.target == existing.target
