"""Opening verification cases.

Which state a case starts in depends on what it verifies (a **proposed** default,
open question in ``docs/data-dictionary/verification.md``):

- a report is created by submitting it, so its case opens in ``submitted``;
- an event is drafted by a moderator before it is put up for review, so its case
  opens in ``draft``;
- an impact claim is recorded with its source in one step, so its case opens in
  ``submitted``.

Patterns: Factory.
"""

from types import MappingProxyType
from typing import Final
from uuid import UUID

from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import CaseAlreadyOpenError
from yakhnama.modules.verification.domain.events import VerificationCaseOpened
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator

_INITIAL_STATES: Final = MappingProxyType(
    {
        TargetKind.REPORT: VerificationState.SUBMITTED,
        TargetKind.EVENT: VerificationState.DRAFT,
        TargetKind.CLAIM: VerificationState.SUBMITTED,
    }
)


def initial_state_for(kind: TargetKind) -> VerificationState:
    """Return the state a new case for a target of ``kind`` starts in.

    Args:
        kind: What the case verifies.

    Returns:
        ``submitted`` for reports and claims, ``draft`` for events (proposed).
    """
    return _INITIAL_STATES[kind]


class VerificationCaseFactory:
    """Opens verification cases in the right initial state.

    Implements: Factory.
    """

    @staticmethod
    def open(
        target: VerificationTarget,
        *,
        opened_by: UUID,
        clock: Clock,
        ids: IdGenerator,
        existing_case: VerificationCase | None = None,
    ) -> AggregateChange[VerificationCase]:
        """Open a case for ``target``, with an empty history.

        One target has exactly one case for life; the handler looks the target up and
        passes what it found as ``existing_case``.

        Args:
            target: The record to verify.
            opened_by: Who opens the case.
            clock: Source of the timestamps.
            ids: Source of the case id and the event id.
            existing_case: The case already open for ``target``, if any.

        Returns:
            The new case at version 1 and a ``VerificationCaseOpened`` event.

        Raises:
            CaseAlreadyOpenError: If ``existing_case`` is given.
        """
        if existing_case is not None:
            raise CaseAlreadyOpenError(target, existing_case.id)
        now = clock.now()
        initial_state = initial_state_for(target.kind)
        case = VerificationCase(
            id=ids.new_id(),
            target=target,
            initial_state=initial_state,
            state=initial_state,
            opened_by=opened_by,
            created_at=now,
            updated_at=now,
        )
        event = VerificationCaseOpened(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=case.id,
            target_kind=target.kind,
            target_id=target.target_id,
            initial_state=initial_state,
            opened_by=opened_by,
        )
        return AggregateChange[VerificationCase](state=case, events=(event,))
