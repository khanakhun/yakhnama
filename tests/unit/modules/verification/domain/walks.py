"""Deterministic clock and ids, and paths that bring a case into any state.

Patterns: Fake.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final

from tests.factories.verification import VerificationCaseTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import VerificationState

OPENED_AT: Final = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
REVIEWER_ID: Final = SequentialIdGenerator(seed=7).new_id()
REASON: Final = "Checked against the district disaster office bulletin."

_S = VerificationState
PATHS: Final[Mapping[VerificationState, tuple[VerificationState, ...]]] = (
    MappingProxyType(
        {
            _S.DRAFT: (),
            _S.SUBMITTED: (),
            _S.UNDER_REVIEW: (_S.UNDER_REVIEW,),
            _S.VERIFIED: (_S.UNDER_REVIEW, _S.VERIFIED),
            _S.REJECTED: (_S.UNDER_REVIEW, _S.REJECTED),
            _S.NEEDS_INFORMATION: (_S.UNDER_REVIEW, _S.NEEDS_INFORMATION),
            _S.DISPUTED: (_S.UNDER_REVIEW, _S.VERIFIED, _S.DISPUTED),
            _S.RETRACTED: (_S.UNDER_REVIEW, _S.VERIFIED, _S.RETRACTED),
        }
    )
)
"""Moves from ``submitted`` (or ``draft`` for ``draft``) that reach each state."""


def stepping_clock(start: datetime = OPENED_AT) -> SteppingClock:
    """Return a clock starting after ``OPENED_AT`` and advancing a minute per call.

    Args:
        start: The instant the case was opened; the first move is one minute later.

    Returns:
        The clock.
    """
    return SteppingClock(start + timedelta(minutes=1), timedelta(minutes=1))


def opened_case(initial_state: VerificationState) -> VerificationCase:
    """Return an unassigned case opened at ``OPENED_AT`` in ``initial_state``.

    Args:
        initial_state: ``draft`` or ``submitted``.

    Returns:
        The case at version 1 with no history.
    """
    return VerificationCaseTestFactory.build(
        initial_state=initial_state,
        state=initial_state,
        created_at=OPENED_AT,
        updated_at=OPENED_AT,
    )


def case_in(
    state: VerificationState,
    clock: SteppingClock | None = None,
    ids: SequentialIdGenerator | None = None,
) -> VerificationCase:
    """Return a case walked by a human reviewer into ``state``.

    Args:
        state: The state to reach.
        clock: The clock to use; a fresh ``stepping_clock`` by default.
        ids: The id source; a fresh generator by default.

    Returns:
        A case whose history is the path in ``PATHS``.
    """
    clock = clock or stepping_clock()
    ids = ids or SequentialIdGenerator(seed=3)
    initial = _S.DRAFT if state is _S.DRAFT else _S.SUBMITTED
    case = opened_case(initial)
    for step in PATHS[state]:
        case = case.transition(
            step,
            actor_id=REVIEWER_ID,
            reason=REASON,
            is_human=True,
            clock=clock,
            ids=ids,
        ).state
    return case
