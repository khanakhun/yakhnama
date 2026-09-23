"""Write requests accepted by the impacts command handlers.

Every command carries the ``actor`` it runs as; the handler asks its
``AuthorisationPolicy`` about that actor before doing anything.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.impacts.domain.reference import ImpactMetricReferenceFile
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    RetirementReason,
)


class LoadReferenceImpactMetrics(BaseModel):
    """Load the impact metric reference file, creating or updating by code.

    Implements: Command.

    Attributes:
        file: The validated reference file.
        actor: Who asks; refused unless the policy explicitly allows them.
        dry_run: Compute the report but roll back instead of committing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: ImpactMetricReferenceFile
    actor: Actor
    dry_run: bool = False


class RetireImpactMetric(BaseModel):
    """Retire an impact metric so new claims can no longer use it.

    Implements: Command.

    Attributes:
        ref: The metric to retire.
        reason: Why, and which metric replaces it, if any.
        actor: Who asks; refused unless the policy explicitly allows them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ImpactMetricRef
    reason: RetirementReason
    actor: Actor
