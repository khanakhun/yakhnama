"""Write requests accepted by the hazards command handlers.

Every command carries ``actor_id`` for the placeholder ``AdminOnlyPolicy``; Phase 2
passes an authenticated actor instead.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.domain.reference import HazardTypeReferenceFile
from yakhnama.modules.hazards.domain.value_objects import (
    HazardTypeRef,
    RetirementReason,
    RetirementText,
)
from yakhnama.shared_kernel.ids import EntityId


class LoadReferenceHazardTypes(BaseModel):
    """Load the hazard taxonomy reference file, creating or updating by code.

    Implements: Command.

    Attributes:
        file: The validated reference file.
        actor_id: Who asks, or ``None`` if unknown (then denied by default).
        dry_run: Compute the report but roll back instead of committing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: HazardTypeReferenceFile
    actor_id: EntityId | None
    dry_run: bool = False


class RetireHazardType(BaseModel):
    """Retire a hazard type so no new events can be classified with it.

    Implements: Command.

    Attributes:
        ref: The hazard type to retire.
        reason: Why, and which code replaces it, if any.
        actor_id: Who asks, or ``None`` if unknown (then denied by default).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef
    reason: RetirementReason
    actor_id: EntityId | None


class ReactivateHazardType(BaseModel):
    """Make a retired hazard type active again.

    Implements: Command.

    Attributes:
        ref: The hazard type to reactivate.
        reason: Why, kept for the audit trail; 1 to 500 characters.
        actor_id: Who asks, or ``None`` if unknown (then denied by default).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef
    reason: RetirementText
    actor_id: EntityId | None
