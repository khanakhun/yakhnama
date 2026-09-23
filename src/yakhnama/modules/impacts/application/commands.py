"""Write requests accepted by the impacts command handlers.

Every command carries ``actor_id`` for the placeholder ``AdminOnlyPolicy``; Phase 2
passes an authenticated actor instead.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceFile
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    RetirementReason,
)
from yakhnama.shared_kernel.ids import EntityId


class LoadReferenceImpactMetrics(BaseModel):
    """Load the impact metric reference file, creating or updating by code.

    Implements: Command.

    Attributes:
        file: The validated reference file.
        actor_id: Who asks, or ``None`` if unknown (then denied by default).
        dry_run: Compute the report but roll back instead of committing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: ImpactMetricReferenceFile
    actor_id: EntityId | None
    dry_run: bool = False


class RetireImpactMetric(BaseModel):
    """Retire an impact metric so new claims can no longer use it.

    Implements: Command.

    Attributes:
        ref: The metric to retire.
        reason: Why, and which metric replaces it, if any.
        actor_id: Who asks, or ``None`` if unknown (then denied by default).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ImpactMetricRef
    reason: RetirementReason
    actor_id: EntityId | None
