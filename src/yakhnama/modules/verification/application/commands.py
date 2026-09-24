"""Write requests accepted by the verification command handlers.

Every command carries the ``actor`` it runs as; the handler asks its
``AuthorisationPolicy`` about that actor before changing anything.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.verification.domain.value_objects import (
    TransitionReason,
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.ids import EntityId


class OpenVerificationCase(BaseModel):
    """Open the verification case of a report, an event or an impact claim.

    Implements: Command.

    Attributes:
        actor: Who opens it: a moderator through the API, or the actor of the use
            case that created the target when another module opens it.
        target: The record to verify.
        if_absent: Return the existing case instead of failing when the target
            already has one; internal callers set it so a retried use case is safe.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    target: VerificationTarget
    if_absent: bool = False


class TransitionVerification(BaseModel):
    """Move a verification case to another state of the transition table.

    Implements: Command.

    Attributes:
        actor: Who moves it.
        case_id: The case.
        to_state: The requested state.
        reason: Why; required for every target state except ``submitted``.
        is_human: Whether a person makes the move. The API always sends ``True``;
            only internal callers (automated checks) send ``False``, and they can
            never reach ``verified``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    case_id: EntityId
    to_state: VerificationState
    reason: TransitionReason | None = None
    is_human: bool = True


class AssignVerificationCase(BaseModel):
    """Make a reviewer responsible for a verification case.

    Implements: Command.

    Attributes:
        actor: Who assigns it.
        case_id: The case.
        reviewer_id: The user who becomes responsible; must be allowed to review.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    case_id: EntityId
    reviewer_id: EntityId
