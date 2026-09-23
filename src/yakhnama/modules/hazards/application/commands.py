"""Write requests accepted by the hazards command handlers.

Every command carries the ``actor`` it runs as; the handler asks its
``AuthorisationPolicy`` about that actor before doing anything.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.domain.reference import HazardTypeReferenceFile
from yakhnama.modules.hazards.domain.value_objects import (
    HazardTypeRef,
    RetirementReason,
    RetirementText,
)
from yakhnama.modules.identity.public import Actor


class LoadReferenceHazardTypes(BaseModel):
    """Load the hazard taxonomy reference file, creating or updating by code.

    Implements: Command.

    Attributes:
        file: The validated reference file.
        actor: Who asks; refused unless the policy explicitly allows them.
        dry_run: Compute the report but roll back instead of committing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: HazardTypeReferenceFile
    actor: Actor
    dry_run: bool = False


class RetireHazardType(BaseModel):
    """Retire a hazard type so no new events can be classified with it.

    Implements: Command.

    Attributes:
        ref: The hazard type to retire.
        reason: Why, and which code replaces it, if any.
        actor: Who asks; refused unless the policy explicitly allows them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef
    reason: RetirementReason
    actor: Actor


class ReactivateHazardType(BaseModel):
    """Make a retired hazard type active again.

    Implements: Command.

    Attributes:
        ref: The hazard type to reactivate.
        reason: Why, kept for the audit trail; 1 to 500 characters.
        actor: Who asks; refused unless the policy explicitly allows them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: HazardTypeRef
    reason: RetirementText
    actor: Actor
