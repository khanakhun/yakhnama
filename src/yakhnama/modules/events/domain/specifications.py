"""Search filters over events, written once and evaluated in two places.

Each leaf is a ``Specification`` over ``EventSearchCandidate``, the flat read-model
view of an event. Fakes and unit tests call ``is_satisfied_by``; the infrastructure
query service walks the same tree with a ``SpecificationVisitor`` and compiles each
leaf to SQL from its public properties. The semantics documented on each leaf are the
contract both sides implement.

Patterns: Specification, Value Object.
"""

from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.events.domain.value_objects import EventPeriod, EventStatus
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardCode
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates

VERIFIED_STATE: Final = "verified"
"""The ``verification`` module's ``VerificationState.VERIFIED`` value.

Held as a string so the events domain does not depend on the verification module;
the read model stores the mirrored state under the same values.
"""


class EventSearchCandidate(BaseModel):
    """The flat view of an event that search filters are evaluated against.

    Implements: Value Object.

    Attributes:
        id: The event.
        centroid: Its representative point, if known.
        hazard_code: Its hazard type code.
        place_codes: Codes of every affected place, whatever its kind.
        status: Its editorial status.
        period: When it happened.
        verification_state: The mirrored state of its verification case, for
            example ``"verified"``, or ``None`` if no case exists yet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    centroid: Coordinates | None
    hazard_code: HazardCode
    place_codes: frozenset[PlaceCode] = frozenset()
    status: EventStatus
    period: EventPeriod
    verification_state: str | None = None


class EventBboxSpecification(Specification[EventSearchCandidate]):
    """Events whose centroid lies inside a box, edges included.

    The centroid, not the geometry, is tested (a **proposed** rule): an extent that
    only grazes the box does not match, and events without a centroid never match.
    SQL: ``centroid && ST_MakeEnvelope(...)`` with the same edge rule.

    Implements: Specification.
    """

    def __init__(self, bbox: BoundingBox) -> None:
        """Create the filter.

        Args:
            bbox: The search box.
        """
        self._bbox = bbox

    @property
    def bbox(self) -> BoundingBox:
        """Return the search box."""
        return self._bbox

    def is_satisfied_by(self, candidate: EventSearchCandidate) -> bool:
        """Tell whether the candidate's centroid is inside the box.

        Args:
            candidate: The event to test.

        Returns:
            ``True`` if it has a centroid inside or on the edge of the box.
        """
        return candidate.centroid is not None and self._bbox.contains(
            candidate.centroid
        )


class EventHazardTypeSpecification(Specification[EventSearchCandidate]):
    """Events of exactly one hazard type code; narrower types are not included.

    Implements: Specification.
    """

    def __init__(self, code: str) -> None:
        """Create the filter.

        Args:
            code: The hazard type code.
        """
        self._code = code

    @property
    def code(self) -> str:
        """Return the hazard type code."""
        return self._code

    def is_satisfied_by(self, candidate: EventSearchCandidate) -> bool:
        """Tell whether the candidate has the hazard type.

        Args:
            candidate: The event to test.

        Returns:
            ``True`` if its hazard code equals ``code``.
        """
        return candidate.hazard_code == self._code


class EventPlaceSpecification(Specification[EventSearchCandidate]):
    """Events that list one place code among their affected places.

    Exact match on the code, any kind; places below it in the hierarchy are not
    included (the application may OR their codes together).

    Implements: Specification.
    """

    def __init__(self, place_code: str) -> None:
        """Create the filter.

        Args:
            place_code: The place code.
        """
        self._place_code = place_code

    @property
    def place_code(self) -> str:
        """Return the place code."""
        return self._place_code

    def is_satisfied_by(self, candidate: EventSearchCandidate) -> bool:
        """Tell whether the candidate concerns the place.

        Args:
            candidate: The event to test.

        Returns:
            ``True`` if ``place_code`` is among its place codes.
        """
        return self._place_code in candidate.place_codes


class EventStatusSpecification(Specification[EventSearchCandidate]):
    """Events with one editorial status.

    Implements: Specification.
    """

    def __init__(self, status: EventStatus) -> None:
        """Create the filter.

        Args:
            status: The status.
        """
        self._status = status

    @property
    def status(self) -> EventStatus:
        """Return the status."""
        return self._status

    def is_satisfied_by(self, candidate: EventSearchCandidate) -> bool:
        """Tell whether the candidate has the status.

        Args:
            candidate: The event to test.

        Returns:
            ``True`` if its status equals ``status``.
        """
        return candidate.status is self._status


class EventPeriodOverlapsSpecification(Specification[EventSearchCandidate]):
    """Events that may have happened within a closed time window.

    Uses ``EventPeriod.overlaps``: the event's span runs from its truncated start to
    the end of the precision period of its end (or of its start when the end is
    unknown). SQL: ``earliest <= :to AND latest >= :from`` on the stored bounds.

    Implements: Specification.
    """

    def __init__(self, start: datetime | None, end: datetime | None) -> None:
        """Create the filter.

        Args:
            start: Lower bound, inclusive, timezone-aware; ``None`` for none.
            end: Upper bound, inclusive, timezone-aware; ``None`` for none.

        Raises:
            ValidationError: If a bound is naive or ``start`` is after ``end``.
        """
        for bound in (start, end):
            if bound is not None and bound.utcoffset() is None:
                message = "period bounds must be timezone-aware"
                raise ValidationError(message)
        self._start = None if start is None else start.astimezone(UTC)
        self._end = None if end is None else end.astimezone(UTC)
        if (
            self._start is not None
            and self._end is not None
            and self._start > self._end
        ):
            message = "the period start must not be after its end"
            raise ValidationError(message)

    @property
    def start(self) -> datetime | None:
        """Return the lower bound, UTC, or ``None``."""
        return self._start

    @property
    def end(self) -> datetime | None:
        """Return the upper bound, UTC, or ``None``."""
        return self._end

    def is_satisfied_by(self, candidate: EventSearchCandidate) -> bool:
        """Tell whether the candidate's period meets the window.

        Args:
            candidate: The event to test.

        Returns:
            ``True`` if the period overlaps ``[start, end]``.
        """
        return candidate.period.overlaps(self._start, self._end)


class VerifiedEventSpecification(Specification[EventSearchCandidate]):
    """Events whose verification case is currently ``verified``.

    Evaluated against the read-model attribute ``verification_state``, which the
    events read model mirrors from ``verification`` domain events; the event
    aggregate itself does not know its verification state.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: EventSearchCandidate) -> bool:
        """Tell whether the candidate is verified.

        Args:
            candidate: The event to test.

        Returns:
            ``True`` if ``verification_state`` is ``"verified"``.
        """
        return candidate.verification_state == VERIFIED_STATE
