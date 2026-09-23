"""Unit tests for ``yakhnama.modules.verification.domain.entities``."""

import itertools
from datetime import timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.ids import SequentialIdGenerator
from tests.unit.modules.verification.domain.walks import (
    OPENED_AT,
    REASON,
    REVIEWER_ID,
    case_in,
    opened_case,
    stepping_clock,
)
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import (
    HumanRequiredError,
    ReasonRequiredError,
)
from yakhnama.modules.verification.domain.events import (
    VerificationAssigned,
    VerificationTransitioned,
)
from yakhnama.modules.verification.domain.state_machine import (
    TRANSITIONS,
    can_transition,
    next_states,
)
from yakhnama.modules.verification.domain.value_objects import (
    Transition,
    VerificationState,
)
from yakhnama.shared_kernel.errors import InvalidTransitionError

S = VerificationState
STATES = st.sampled_from(list(VerificationState))
FORBIDDEN_PAIRS = st.tuples(STATES, STATES).filter(
    lambda pair: not can_transition(*pair)
)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)


def _move(
    case: VerificationCase,
    to_state: VerificationState,
    *,
    reason: str | None = REASON,
    is_human: bool = True,
) -> VerificationCase:
    return case.transition(
        to_state,
        actor_id=REVIEWER_ID,
        reason=reason,
        is_human=is_human,
        clock=stepping_clock(case.updated_at),
        ids=SequentialIdGenerator(seed=5),
    ).state


def _fields(case: VerificationCase, **overrides: object) -> dict[str, object]:
    fields = dict(case)
    fields.update(overrides)
    return fields


# --------------------------------------------------------------------------- #
# transition                                                                  #
# --------------------------------------------------------------------------- #


def test_transition_allowed_move_appends_history_and_bumps_version() -> None:
    case = opened_case(S.SUBMITTED)
    clock = stepping_clock()

    change = case.transition(
        S.UNDER_REVIEW,
        actor_id=REVIEWER_ID,
        reason=REASON,
        is_human=True,
        clock=clock,
        ids=SequentialIdGenerator(seed=1),
    )

    moved = change.state
    assert moved.state is S.UNDER_REVIEW
    assert moved.version == case.version + 1
    assert moved.history == (
        Transition(
            from_state=S.SUBMITTED,
            to_state=S.UNDER_REVIEW,
            actor_id=REVIEWER_ID,
            reason=REASON,
            occurred_at=moved.updated_at,
            is_human=True,
        ),
    )
    assert moved.updated_at == OPENED_AT + timedelta(minutes=1)


def test_transition_allowed_move_emits_event_without_reason_text() -> None:
    case = opened_case(S.SUBMITTED)

    change = case.transition(
        S.UNDER_REVIEW,
        actor_id=REVIEWER_ID,
        reason=REASON,
        is_human=True,
        clock=stepping_clock(),
        ids=SequentialIdGenerator(seed=1),
    )

    (event,) = change.events
    assert isinstance(event, VerificationTransitioned)
    assert event.event_type == "verification.verification_transitioned"
    assert event.aggregate_id == case.id
    assert event.target_kind is case.target.kind
    assert event.target_id == case.target.target_id
    assert (event.from_state, event.to_state) == (S.SUBMITTED, S.UNDER_REVIEW)
    assert event.actor_id == REVIEWER_ID
    assert event.is_human
    assert event.has_reason
    assert REASON not in event.model_dump_json()


@given(pair=FORBIDDEN_PAIRS)
def test_transition_any_pair_outside_table_raises_invalid_transition(
    pair: tuple[VerificationState, VerificationState],
) -> None:
    from_state, to_state = pair
    case = case_in(from_state)

    with pytest.raises(InvalidTransitionError) as raised:
        _move(case, to_state)

    assert raised.value.details == {
        "from_state": from_state.value,
        "to_state": to_state.value,
    }


def test_transition_to_verified_by_automation_raises_human_required() -> None:
    case = case_in(S.UNDER_REVIEW)

    with pytest.raises(HumanRequiredError) as raised:
        _move(case, S.VERIFIED, is_human=False)

    assert raised.value.to_state is S.VERIFIED


def test_transition_to_rejected_by_automation_is_allowed() -> None:
    case = case_in(S.UNDER_REVIEW)

    moved = _move(case, S.REJECTED, is_human=False)

    assert moved.state is S.REJECTED
    assert not moved.history[-1].is_human


@pytest.mark.parametrize("reason", [None, "", "  \r\n "])
def test_transition_missing_or_blank_reason_raises_reason_required(
    reason: str | None,
) -> None:
    case = case_in(S.VERIFIED)

    with pytest.raises(ReasonRequiredError) as raised:
        _move(case, S.RETRACTED, reason=reason)

    assert raised.value.to_state is S.RETRACTED


def test_transition_to_submitted_without_reason_is_allowed() -> None:
    case = opened_case(S.DRAFT)

    moved = _move(case, S.SUBMITTED, reason=None)

    assert moved.state is S.SUBMITTED
    assert moved.history[-1].reason is None


def test_transition_to_submitted_blank_reason_stored_as_none() -> None:
    case = opened_case(S.DRAFT)

    change = case.transition(
        S.SUBMITTED,
        actor_id=REVIEWER_ID,
        reason="   ",
        is_human=True,
        clock=stepping_clock(),
        ids=SequentialIdGenerator(seed=1),
    )

    assert change.state.history[-1].reason is None
    (event,) = change.events
    assert isinstance(event, VerificationTransitioned)
    assert not event.has_reason


def test_transition_unsafe_reason_raises_pydantic_error() -> None:
    case = case_in(S.UNDER_REVIEW)

    with pytest.raises(PydanticValidationError):
        _move(case, S.REJECTED, reason=f"spoof{RIGHT_TO_LEFT_OVERRIDE}text")


def test_transition_does_not_change_original_case() -> None:
    case = case_in(S.UNDER_REVIEW)

    _move(case, S.VERIFIED)

    assert case.state is S.UNDER_REVIEW


@given(choices=st.lists(st.integers(min_value=0, max_value=5), max_size=25))
def test_transition_random_walk_stays_consistent_with_history(
    choices: list[int],
) -> None:
    case = opened_case(S.DRAFT)
    clock = stepping_clock()
    ids = SequentialIdGenerator(seed=9)

    for choice in choices:
        targets = sorted(next_states(case.state))
        if not targets:
            break
        case = case.transition(
            targets[choice % len(targets)],
            actor_id=REVIEWER_ID,
            reason=REASON,
            is_human=True,
            clock=clock,
            ids=ids,
        ).state

    assert case.version == len(case.history) + 1
    assert case.state is (case.history[-1].to_state if case.history else S.DRAFT)
    for earlier, later in itertools.pairwise(case.history):
        assert earlier.to_state is later.from_state
    for step in case.history:
        assert step.to_state in TRANSITIONS[step.from_state]
        if step.to_state is S.VERIFIED:
            assert step.from_state is S.UNDER_REVIEW
        if step.to_state is S.RETRACTED:
            assert step.from_state is S.VERIFIED
            assert step.reason is not None
    assert VerificationCase.model_validate(case.model_dump()) == case


# --------------------------------------------------------------------------- #
# history invariants on construction                                          #
# --------------------------------------------------------------------------- #


def test_verification_case_initial_state_outside_entry_states_rejected() -> None:
    case = opened_case(S.SUBMITTED)

    with pytest.raises(PydanticValidationError, match="opened in draft or submitted"):
        VerificationCase.model_validate(
            _fields(case, initial_state=S.UNDER_REVIEW, state=S.UNDER_REVIEW)
        )


def test_verification_case_state_not_matching_history_rejected() -> None:
    case = case_in(S.UNDER_REVIEW)

    with pytest.raises(PydanticValidationError, match="to_state of the last"):
        VerificationCase.model_validate(_fields(case, state=S.VERIFIED))


def test_verification_case_broken_chain_rejected() -> None:
    case = case_in(S.UNDER_REVIEW)
    wrong = case.history[0].model_copy(update={"from_state": S.DRAFT})

    with pytest.raises(PydanticValidationError, match="start where the previous"):
        VerificationCase.model_validate(_fields(case, history=(wrong,)))


def test_verification_case_move_outside_table_in_history_rejected() -> None:
    case = opened_case(S.SUBMITTED)
    step = Transition(
        from_state=S.SUBMITTED,
        to_state=S.VERIFIED,
        actor_id=REVIEWER_ID,
        reason=REASON,
        occurred_at=OPENED_AT,
        is_human=True,
    )

    with pytest.raises(PydanticValidationError, match="not in the table"):
        VerificationCase.model_validate(
            _fields(case, history=(step,), state=S.VERIFIED)
        )


def test_verification_case_automated_verification_in_history_rejected() -> None:
    case = case_in(S.VERIFIED)
    automated = case.history[-1].model_copy(update={"is_human": False})

    with pytest.raises(PydanticValidationError, match="only a person"):
        VerificationCase.model_validate(
            _fields(case, history=(case.history[0], automated))
        )


def test_verification_case_reasonless_retraction_in_history_rejected() -> None:
    case = case_in(S.RETRACTED)
    reasonless = case.history[-1].model_copy(update={"reason": None})

    with pytest.raises(PydanticValidationError, match="requires a reason"):
        VerificationCase.model_validate(
            _fields(case, history=(*case.history[:-1], reasonless))
        )


def test_verification_case_out_of_order_history_rejected() -> None:
    case = case_in(S.UNDER_REVIEW)
    early = case.history[0].model_copy(
        update={"occurred_at": OPENED_AT - timedelta(seconds=1)}
    )

    with pytest.raises(PydanticValidationError, match="chronological"):
        VerificationCase.model_validate(_fields(case, history=(early,)))


def test_verification_case_updated_before_last_change_rejected() -> None:
    case = case_in(S.UNDER_REVIEW)

    with pytest.raises(PydanticValidationError, match="updated_at"):
        VerificationCase.model_validate(_fields(case, updated_at=OPENED_AT))


def test_verification_case_offset_timestamps_normalised_to_utc() -> None:
    case = opened_case(S.SUBMITTED)
    local = OPENED_AT.astimezone(timezone(timedelta(hours=5)))

    rebuilt = VerificationCase.model_validate(
        _fields(case, created_at=local, updated_at=local)
    )

    assert rebuilt.created_at.utcoffset() == timedelta(0)
    assert rebuilt.created_at == OPENED_AT


# --------------------------------------------------------------------------- #
# properties                                                                  #
# --------------------------------------------------------------------------- #


@given(state=STATES)
def test_verification_case_is_verified_only_in_verified(
    state: VerificationState,
) -> None:
    case = case_in(state)

    is_verified = case.is_verified

    assert is_verified == (state is S.VERIFIED)


@given(state=STATES)
def test_verification_case_is_closed_only_when_terminal(
    state: VerificationState,
) -> None:
    case = case_in(state)

    is_closed = case.is_closed

    assert is_closed == (state in {S.REJECTED, S.RETRACTED})


# --------------------------------------------------------------------------- #
# assign                                                                      #
# --------------------------------------------------------------------------- #


def test_assign_new_reviewer_sets_assignee_and_emits_event() -> None:
    case = opened_case(S.SUBMITTED)
    assigner = SequentialIdGenerator(seed=21).new_id()

    change = case.assign(
        REVIEWER_ID,
        actor_id=assigner,
        clock=stepping_clock(),
        ids=SequentialIdGenerator(seed=1),
    )

    assert change.state.assigned_to == REVIEWER_ID
    assert change.state.version == case.version + 1
    (event,) = change.events
    assert isinstance(event, VerificationAssigned)
    assert event.event_type == "verification.verification_assigned"
    assert event.reviewer_id == REVIEWER_ID
    assert event.previous_reviewer_id is None
    assert event.assigned_by == assigner


def test_assign_other_reviewer_records_previous_reviewer() -> None:
    other = SequentialIdGenerator(seed=22).new_id()
    case = opened_case(S.SUBMITTED)
    assigned = case.assign(
        REVIEWER_ID, actor_id=other, clock=stepping_clock(), ids=SequentialIdGenerator()
    ).state

    change = assigned.assign(
        other,
        actor_id=other,
        clock=stepping_clock(assigned.updated_at),
        ids=SequentialIdGenerator(seed=2),
    )

    (event,) = change.events
    assert isinstance(event, VerificationAssigned)
    assert event.previous_reviewer_id == REVIEWER_ID
    assert change.state.assigned_to == other


def test_assign_same_reviewer_returns_unchanged_case_without_event() -> None:
    case = opened_case(S.SUBMITTED)
    assigned = case.assign(
        REVIEWER_ID,
        actor_id=REVIEWER_ID,
        clock=stepping_clock(),
        ids=SequentialIdGenerator(),
    ).state

    change = assigned.assign(
        REVIEWER_ID,
        actor_id=REVIEWER_ID,
        clock=stepping_clock(),
        ids=SequentialIdGenerator(),
    )

    assert change.state == assigned
    assert change.events == ()


@pytest.mark.parametrize("state", [S.REJECTED, S.RETRACTED])
def test_assign_closed_case_raises_invalid_transition(
    state: VerificationState,
) -> None:
    case = case_in(state)

    with pytest.raises(InvalidTransitionError) as raised:
        case.assign(
            REVIEWER_ID,
            actor_id=REVIEWER_ID,
            clock=stepping_clock(case.updated_at),
            ids=SequentialIdGenerator(),
        )

    assert raised.value.details == {"state": state.value}
