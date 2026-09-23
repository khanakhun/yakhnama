"""Write requests accepted by the geography command handlers.

Every command carries the ``actor`` it runs as; the handler asks its
``AuthorisationPolicy`` about that actor before doing anything.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.geography.domain.reference import PlaceReferenceFile
from yakhnama.modules.geography.domain.value_objects import StatusReason
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId


class LoadReferencePlaces(BaseModel):
    """Load a place reference file, creating or updating places by code.

    Implements: Command.

    Attributes:
        file: The validated reference file.
        actor: Who asks; refused unless the policy explicitly allows them.
        dry_run: Compute the report but roll back instead of committing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: PlaceReferenceFile
    actor: Actor
    dry_run: bool = False


class RetirePlace(BaseModel):
    """Retire a place that no longer exists as an administrative unit.

    Implements: Command.

    Attributes:
        place_id: The place to retire.
        reason: Why, 1 to 500 characters.
        actor: Who asks; refused unless the policy explicitly allows them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_id: EntityId
    reason: StatusReason
    actor: Actor


class MergePlace(BaseModel):
    """Merge a place into another place that replaces it.

    Implements: Command.

    Attributes:
        place_id: The place that is merged away.
        target_id: The active place that replaces it.
        reason: Why, 1 to 500 characters.
        actor: Who asks; refused unless the policy explicitly allows them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_id: EntityId
    target_id: EntityId
    reason: StatusReason
    actor: Actor
