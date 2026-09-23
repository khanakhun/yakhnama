"""Read requests accepted by the events query services.

Patterns: Query, Specification.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict

from yakhnama.modules.events.application.authorisation import public_visibility
from yakhnama.modules.events.domain.specifications import (
    EventBboxSpecification,
    EventHazardTypeSpecification,
    EventPeriodOverlapsSpecification,
    EventPlaceSpecification,
    EventSearchCandidate,
    EventStatusSpecification,
    VerifiedEventSpecification,
)
from yakhnama.modules.events.domain.value_objects import EventStatus
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.modules.identity.public import Actor
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import Specification, TrueSpecification
from yakhnama.shared_kernel.value_objects import BoundingBox


class ListEvents(BaseModel):
    """Ask for one page of events matching every set filter.

    Filters left ``None`` match every event. Whatever is asked, an actor who may not
    moderate only ever sees events that are ``published`` and ``verified``; for
    such an actor ``verified_only`` cannot be switched off.

    Implements: Query.

    Attributes:
        actor: Who asks; decides the visibility rule.
        bbox: Only events whose centroid lies inside this box.
        hazard_type: Only events of exactly this hazard type code.
        place_code: Only events listing this place among their affected places.
        status: Only events with this editorial status.
        period_from: Only events that may have lasted until at least this instant.
        period_to: Only events that may have started by this instant.
        verified_only: Only verified events; ``None`` means the default for the
            actor: ``True`` unless the actor may moderate.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    bbox: BoundingBox | None = None
    hazard_type: HazardCode | None = None
    place_code: PlaceCode | None = None
    status: EventStatus | None = None
    period_from: AwareDatetime | None = None
    period_to: AwareDatetime | None = None
    verified_only: bool | None = None
    page: PageRequest = PageRequest()

    def to_specification(
        self, *, may_moderate: bool
    ) -> Specification[EventSearchCandidate]:
        """Combine the set filters and the visibility rule into one specification.

        Args:
            may_moderate: Whether the moderation policy allows ``actor``.

        Returns:
            The conjunction of every set filter, narrowed to published and
            verified events unless ``may_moderate``; ``TrueSpecification`` when
            nothing narrows the search.

        Raises:
            ValidationError: If ``period_from`` is after ``period_to``.
        """
        filters: list[Specification[EventSearchCandidate]] = []
        if self.bbox is not None:
            filters.append(EventBboxSpecification(self.bbox))
        if self.hazard_type is not None:
            filters.append(EventHazardTypeSpecification(self.hazard_type))
        if self.place_code is not None:
            filters.append(EventPlaceSpecification(self.place_code))
        if self.status is not None:
            filters.append(EventStatusSpecification(self.status))
        if self.period_from is not None or self.period_to is not None:
            filters.append(
                EventPeriodOverlapsSpecification(self.period_from, self.period_to)
            )
        if not may_moderate:
            filters.append(public_visibility())
        elif self.verified_only:
            filters.append(VerifiedEventSpecification())
        combined: Specification[EventSearchCandidate] = TrueSpecification()
        for specification in filters:
            combined = (
                specification
                if isinstance(combined, TrueSpecification)
                else combined.and_(specification)
            )
        return combined


class GetEvent(BaseModel):
    """Ask for one event.

    Implements: Query.

    Attributes:
        actor: Who asks; a non-moderator only sees published, verified events.
        event_id: The event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId


class GetEventTimeline(BaseModel):
    """Ask for the dated history of one event.

    Implements: Query.

    Attributes:
        actor: Who asks; the same visibility rule as ``GetEvent``.
        event_id: The event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    event_id: EntityId
