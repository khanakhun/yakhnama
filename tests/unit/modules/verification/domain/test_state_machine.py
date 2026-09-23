"""Unit tests for ``yakhnama.modules.verification.domain.state_machine``."""

from hypothesis import given
from hypothesis import strategies as st

from yakhnama.modules.verification.domain.state_machine import (
    TRANSITIONS,
    can_transition,
    is_terminal,
    next_states,
    requires_human,
    requires_reason,
)
from yakhnama.modules.verification.domain.value_objects import VerificationState

S = VerificationState
EXPECTED_TABLE = {
    S.DRAFT: {S.SUBMITTED},
    S.SUBMITTED: {S.UNDER_REVIEW},
    S.UNDER_REVIEW: {S.VERIFIED, S.REJECTED, S.NEEDS_INFORMATION},
    S.NEEDS_INFORMATION: {S.SUBMITTED},
    S.VERIFIED: {S.DISPUTED, S.RETRACTED},
    S.DISPUTED: {S.UNDER_REVIEW},
    S.REJECTED: set(),
    S.RETRACTED: set(),
}
STATES = st.sampled_from(list(VerificationState))


def test_transitions_table_is_exactly_the_agents_table() -> None:
    table = {state: set(targets) for state, targets in TRANSITIONS.items()}

    assert table == EXPECTED_TABLE


def test_transitions_table_covers_every_state_as_key() -> None:
    keys = set(TRANSITIONS)

    assert keys == set(VerificationState)


@given(from_state=STATES, to_state=STATES)
def test_can_transition_any_pair_matches_table(
    from_state: VerificationState, to_state: VerificationState
) -> None:
    allowed = can_transition(from_state, to_state)

    assert allowed == (to_state in EXPECTED_TABLE[from_state])


@given(state=STATES)
def test_can_transition_same_state_never_allowed(state: VerificationState) -> None:
    allowed = can_transition(state, state)

    assert not allowed


def test_can_transition_into_verified_only_from_under_review() -> None:
    sources = {state for state in S if can_transition(state, S.VERIFIED)}

    assert sources == {S.UNDER_REVIEW}


def test_can_transition_into_retracted_only_from_verified() -> None:
    sources = {state for state in S if can_transition(state, S.RETRACTED)}

    assert sources == {S.VERIFIED}


@given(state=STATES)
def test_next_states_any_state_returns_table_row(state: VerificationState) -> None:
    targets = next_states(state)

    assert targets == frozenset(EXPECTED_TABLE[state])


@given(state=STATES)
def test_requires_reason_any_state_true_except_submitted(
    state: VerificationState,
) -> None:
    needs_reason = requires_reason(state)

    assert needs_reason == (state is not S.SUBMITTED)


def test_requires_reason_retracted_is_true() -> None:
    needs_reason = requires_reason(S.RETRACTED)

    assert needs_reason


@given(state=STATES)
def test_requires_human_any_state_true_only_for_verified(
    state: VerificationState,
) -> None:
    needs_human = requires_human(state)

    assert needs_human == (state is S.VERIFIED)


@given(state=STATES)
def test_is_terminal_any_state_true_only_for_rejected_and_retracted(
    state: VerificationState,
) -> None:
    terminal = is_terminal(state)

    assert terminal == (state in {S.REJECTED, S.RETRACTED})
