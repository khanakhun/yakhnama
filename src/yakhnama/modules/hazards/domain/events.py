"""Domain events of the hazards bounded context.

Every event concerns one ``HazardType`` aggregate (``aggregate_type="hazard_type"``)
and carries the hazard code, so subscribers never need to load the aggregate to know
which taxonomy node changed. ``event_type`` strings are stable once published.

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from yakhnama.modules.hazards.domain.value_objects import (
    HazardCode,
    RetirementText,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.value_objects import LocalizedText

HAZARD_TYPE_AGGREGATE: Final = "hazard_type"


class HazardTypeEvent(DomainEvent):
    """Fields shared by every hazard type event; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"hazard_type"``.
        code: Code of the hazard type that changed.
    """

    aggregate_type: Literal["hazard_type"] = HAZARD_TYPE_AGGREGATE
    code: HazardCode


class HazardTypeCreated(HazardTypeEvent):
    """A hazard type was added to the taxonomy.

    Implements: Domain Events.

    Attributes:
        parent_code: Code of the broader type, or ``None`` for a root.
        attributes_schema: Registry code of its attribute schema, if any.
    """

    event_type: ClassVar[str] = "hazards.hazard_type_created"

    parent_code: HazardCode | None
    attributes_schema: HazardCode | None


class HazardTypeRetired(HazardTypeEvent):
    """A hazard type stopped accepting new classifications; its code stays reserved.

    Implements: Domain Events.

    Attributes:
        reason: Why it was retired.
        replaced_by: Code to use instead, if any.
    """

    event_type: ClassVar[str] = "hazards.hazard_type_retired"

    reason: RetirementText
    replaced_by: HazardCode | None


class HazardTypeReactivated(HazardTypeEvent):
    """A retired hazard type was made active again.

    Implements: Domain Events.

    Attributes:
        reason: Why it was reactivated.
    """

    event_type: ClassVar[str] = "hazards.hazard_type_reactivated"

    reason: RetirementText


class HazardTypeRelabelled(HazardTypeEvent):
    """A hazard type's display labels changed.

    Implements: Domain Events.

    Attributes:
        labels: The new labels, in full.
    """

    event_type: ClassVar[str] = "hazards.hazard_type_relabelled"

    labels: LocalizedText


class HazardTypeReparented(HazardTypeEvent):
    """A hazard type moved under a different parent in the taxonomy.

    Implements: Domain Events.

    Attributes:
        previous_parent_code: The parent before the move, or ``None`` if it was a root.
        parent_code: The parent after the move, or ``None`` if it is now a root.
    """

    event_type: ClassVar[str] = "hazards.hazard_type_reparented"

    previous_parent_code: HazardCode | None
    parent_code: HazardCode | None
