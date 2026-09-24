"""Read models returned by the verification query services and handlers.

DTOs are frozen and carry only what readers need. ``from_entity`` builds them from the
aggregate for in-memory implementations and handler results; the SQL query service
builds them from selected columns with the same field meanings.

Patterns: DTO.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.state_machine import next_states
from yakhnama.modules.verification.domain.value_objects import (
    Transition,
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.ids import EntityId


class VerificationCaseSummary(BaseModel):
    """One verification case in a listing.

    Implements: DTO.

    Attributes:
        id: The case.
        target: The record under verification.
        state: The current state.
        assigned_to: The responsible reviewer, if any.
        version: Optimistic-concurrency version.
        created_at: When the case was opened, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    target: VerificationTarget
    state: VerificationState
    assigned_to: EntityId | None
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, case: VerificationCase) -> Self:
        """Build the summary of a case.

        Args:
            case: The aggregate.

        Returns:
            Its summary.
        """
        return cls(
            id=case.id,
            target=case.target,
            state=case.state,
            assigned_to=case.assigned_to,
            version=case.version,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )


class VerificationCaseDetail(BaseModel):
    """One verification case with its whole history, for moderators.

    Implements: DTO.

    Attributes:
        id: The case.
        target: The record under verification.
        initial_state: The state it was opened in.
        state: The current state.
        next_states: The states the table allows from ``state``, sorted by value.
        history: Every transition, oldest first, with actor and reason.
        assigned_to: The responsible reviewer, if any.
        opened_by: Who opened the case.
        version: Optimistic-concurrency version.
        created_at: When the case was opened, UTC.
        updated_at: When it last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    target: VerificationTarget
    initial_state: VerificationState
    state: VerificationState
    next_states: tuple[VerificationState, ...]
    history: tuple[Transition, ...]
    assigned_to: EntityId | None
    opened_by: EntityId
    version: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def from_entity(cls, case: VerificationCase) -> Self:
        """Build the detail view of a case.

        Args:
            case: The aggregate.

        Returns:
            Its detail view.
        """
        return cls(
            id=case.id,
            target=case.target,
            initial_state=case.initial_state,
            state=case.state,
            next_states=tuple(sorted(next_states(case.state))),
            history=case.history,
            assigned_to=case.assigned_to,
            opened_by=case.opened_by,
            version=case.version,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )
