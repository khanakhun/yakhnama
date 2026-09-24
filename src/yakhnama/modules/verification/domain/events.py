"""Domain events of the verification bounded context.

Every event concerns one ``VerificationCase`` (``aggregate_type="verification_case"``)
and names its target, so subscribers (the audit log, the events read model that mirrors
``verification_state``) never need to load the case. Payloads carry identifiers and
state names only: reason text stays in the case history and never enters the outbox
(Phase 2 security review: ids and non-personal fields only).

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

VERIFICATION_CASE_AGGREGATE: Final = "verification_case"


class VerificationCaseEvent(DomainEvent):
    """Fields shared by every verification event; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"verification_case"``.
        target_kind: Kind of the record under verification.
        target_id: Identifier of that record.
    """

    aggregate_type: Literal["verification_case"] = VERIFICATION_CASE_AGGREGATE
    target_kind: TargetKind
    target_id: EntityId


class VerificationCaseOpened(VerificationCaseEvent):
    """A verification case was opened for a target.

    Implements: Domain Events.

    Attributes:
        initial_state: The state the case starts in.
        opened_by: Who opened it.
    """

    event_type: ClassVar[str] = "verification.verification_case_opened"

    initial_state: VerificationState
    opened_by: EntityId


class VerificationTransitioned(VerificationCaseEvent):
    """A verification case moved from one state to another.

    Implements: Domain Events.

    Attributes:
        from_state: State before the move.
        to_state: State after the move.
        actor_id: Who made the move.
        is_human: Whether a person made it.
        has_reason: Whether a reason was recorded; the text itself is not published.
    """

    event_type: ClassVar[str] = "verification.verification_transitioned"

    from_state: VerificationState
    to_state: VerificationState
    actor_id: EntityId
    is_human: bool
    has_reason: bool


class VerificationAssigned(VerificationCaseEvent):
    """A verification case was assigned to a reviewer.

    Implements: Domain Events.

    Attributes:
        reviewer_id: The new reviewer.
        previous_reviewer_id: The reviewer before, if any.
        assigned_by: Who made the assignment.
    """

    event_type: ClassVar[str] = "verification.verification_assigned"

    reviewer_id: EntityId
    previous_reviewer_id: EntityId | None
    assigned_by: EntityId
