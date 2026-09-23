"""Read requests accepted by the verification query services.

Patterns: Query.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest


class GetVerificationCase(BaseModel):
    """Ask for one verification case with its history.

    Implements: Query.

    Attributes:
        actor: Who asks; only moderators may read cases.
        case_id: The case.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    case_id: EntityId


class ListVerificationCases(BaseModel):
    """Ask for one page of verification cases, oldest first.

    Every filter left ``None`` matches all cases; set filters are combined with AND.

    Implements: Query.

    Attributes:
        actor: Who asks; only moderators may list cases.
        state: Only cases currently in this state.
        target_kind: Only cases about this kind of record.
        assigned_to: Only cases assigned to this reviewer.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    state: VerificationState | None = None
    target_kind: TargetKind | None = None
    assigned_to: EntityId | None = None
    page: PageRequest = PageRequest()
