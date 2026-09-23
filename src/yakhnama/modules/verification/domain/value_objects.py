"""Value objects of the ``verification`` bounded context.

A verification case follows one target (a report, an event or an impact claim) through
the states of ``AGENTS.md`` §6.3. The states and the transition record live here; the
transition table itself lives in ``state_machine``.

Patterns: Value Object.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text

TRANSITION_REASON_MAX_LENGTH: Final = 1000


class VerificationState(StrEnum):
    """Where a target stands in the verification lifecycle (``AGENTS.md`` §6.3).

    Implements: Value Object.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    VERIFIED = "verified"
    REJECTED = "rejected"
    NEEDS_INFORMATION = "needs_information"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class TargetKind(StrEnum):
    """What kind of record a verification case is about.

    Implements: Value Object.
    """

    REPORT = "report"
    EVENT = "event"
    CLAIM = "claim"


class VerificationTarget(BaseModel):
    """The record a verification case is attached to.

    Implements: Value Object.

    Attributes:
        kind: Report, event or impact claim.
        target_id: Identifier of that record (UUIDv7).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TargetKind
    target_id: EntityId


TransitionReason = Annotated[
    str, *safe_text(TRANSITION_REASON_MAX_LENGTH, allow_line_breaks=True)
]
"""Why a case moved: safe text of 1 to 1000 characters; line breaks allowed."""


class Transition(BaseModel):
    """One recorded move of a verification case from one state to another.

    The rules a transition must obey (the table, the reason, the human actor) are
    checked by ``VerificationCase`` over its whole history, where the order of
    transitions is known; this value only holds one step.

    Implements: Value Object.

    Attributes:
        from_state: State before the move.
        to_state: State after the move.
        actor_id: Who made the move: a user, or the service account of an automated
            process when ``is_human`` is ``False``.
        reason: Why; required for every target state except ``submitted``.
        occurred_at: When the move happened, UTC.
        is_human: ``True`` when a person made the move; only a person may verify.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_state: VerificationState
    to_state: VerificationState
    actor_id: EntityId
    reason: TransitionReason | None = None
    occurred_at: AwareDatetime
    is_human: bool

    @field_validator("occurred_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)
