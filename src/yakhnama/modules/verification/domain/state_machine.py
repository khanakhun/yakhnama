"""The verification transition table (``AGENTS.md`` §6.3) and the rules around it.

The table is the single source of truth: ``VerificationCase.transition`` consults it,
the API can list ``next_states`` for a moderator, and property tests check that no
move outside it is ever accepted. It reads::

    draft             -> submitted
    submitted         -> under_review
    under_review      -> verified | rejected | needs_information
    needs_information -> submitted
    verified          -> disputed | retracted
    disputed          -> under_review

``rejected`` and ``retracted`` have no outgoing transition. That they are terminal is
how this module reads the §6.3 table, which lists none; reopening a rejected report is
an open question (a correction is a new report revision, not a state change).

Every move needs an actor, and a reason unless the target state is ``submitted``
(submitting says nothing a reason could add). Only a human may move a case to
``verified``: automated checks may suggest, never verify.

Patterns: State.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from yakhnama.modules.verification.domain.value_objects import VerificationState

TRANSITIONS: Final[Mapping[VerificationState, frozenset[VerificationState]]] = (
    MappingProxyType(
        {
            VerificationState.DRAFT: frozenset({VerificationState.SUBMITTED}),
            VerificationState.SUBMITTED: frozenset({VerificationState.UNDER_REVIEW}),
            VerificationState.UNDER_REVIEW: frozenset(
                {
                    VerificationState.VERIFIED,
                    VerificationState.REJECTED,
                    VerificationState.NEEDS_INFORMATION,
                }
            ),
            VerificationState.NEEDS_INFORMATION: frozenset(
                {VerificationState.SUBMITTED}
            ),
            VerificationState.VERIFIED: frozenset(
                {VerificationState.DISPUTED, VerificationState.RETRACTED}
            ),
            VerificationState.DISPUTED: frozenset({VerificationState.UNDER_REVIEW}),
            VerificationState.REJECTED: frozenset(),
            VerificationState.RETRACTED: frozenset(),
        }
    )
)
"""Allowed target states per state; every ``VerificationState`` is a key."""

_REASONLESS_STATES: Final = frozenset({VerificationState.SUBMITTED})
_HUMAN_ONLY_STATES: Final = frozenset({VerificationState.VERIFIED})


def next_states(from_state: VerificationState) -> frozenset[VerificationState]:
    """Return the states a case in ``from_state`` may move to.

    Args:
        from_state: The current state.

    Returns:
        The allowed target states; empty for ``rejected`` and ``retracted``.
    """
    return TRANSITIONS[from_state]


def can_transition(from_state: VerificationState, to_state: VerificationState) -> bool:
    """Tell whether the table allows moving from ``from_state`` to ``to_state``.

    Args:
        from_state: The current state.
        to_state: The requested state.

    Returns:
        ``True`` if the move is in ``TRANSITIONS``.
    """
    return to_state in TRANSITIONS[from_state]


def requires_reason(to_state: VerificationState) -> bool:
    """Tell whether a move into ``to_state`` must carry a reason.

    Args:
        to_state: The requested state.

    Returns:
        ``True`` for every state except ``submitted``.
    """
    return to_state not in _REASONLESS_STATES


def requires_human(to_state: VerificationState) -> bool:
    """Tell whether only a person may move a case into ``to_state``.

    Args:
        to_state: The requested state.

    Returns:
        ``True`` for ``verified``.
    """
    return to_state in _HUMAN_ONLY_STATES


def is_terminal(state: VerificationState) -> bool:
    """Tell whether no transition leaves ``state``.

    Args:
        state: A state.

    Returns:
        ``True`` for ``rejected`` and ``retracted``.
    """
    return not TRANSITIONS[state]
