"""Creation of new ``Place`` aggregates.

The hierarchy rule needs the parent's *level*, which a ``Place`` does not hold for its
parent (it stores only ``parent_id``), so it is enforced here, where the caller hands
in the loaded parent.

Patterns: Factory, Value Object.
"""

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.geography.domain.entities import PLACE_MAX_NAMES, Place
from yakhnama.modules.geography.domain.errors import (
    InvalidPlaceHierarchyError,
    PlaceRetiredError,
)
from yakhnama.modules.geography.domain.events import PlaceCreated
from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    PlaceCode,
    PlaceGeometry,
    PlaceName,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator
from yakhnama.shared_kernel.value_objects import Coordinates


class PlaceDraft(BaseModel):
    """Everything needed to create a place, before it has an id.

    The parent is named by code so that reference data, which has no ids, can
    describe a hierarchy; the caller resolves the code to a ``Place`` before calling
    ``PlaceFactory.create``.

    Implements: Value Object.

    Attributes:
        code: Stable machine code of the new place.
        level: Administrative level.
        parent_code: Code of the enclosing place; ``None`` only for a country.
        names: At least one name; the ``Place`` name invariants apply.
        geometry: WGS84 footprint, if known.
        centroid: Representative WGS84 point, if known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PlaceCode
    level: AdminLevel
    parent_code: PlaceCode | None = None
    names: tuple[PlaceName, ...] = Field(min_length=1, max_length=PLACE_MAX_NAMES)
    geometry: PlaceGeometry | None = None
    centroid: Coordinates | None = None


class PlaceFactory:
    """Create new places with a valid position in the hierarchy.

    Implements: Factory.
    """

    def create(
        self,
        draft: PlaceDraft,
        *,
        parent: Place | None,
        ids: IdGenerator,
        clock: Clock,
    ) -> AggregateChange[Place]:
        """Create a place at version 1 and the ``PlaceCreated`` event.

        Args:
            draft: The new place's data.
            parent: The loaded parent place whose code is ``draft.parent_code``, or
                ``None`` for a country.
            ids: Source of the place id and the event id.
            clock: Source of ``created_at``, ``updated_at`` and ``occurred_at``.

        Returns:
            The new place and its ``PlaceCreated`` event.

        Raises:
            InvalidPlaceHierarchyError: If ``parent`` does not match
                ``draft.parent_code``, or the parent's level is not strictly higher
                (``AdminLevel.can_be_child_of``, a proposed rule).
            PlaceRetiredError: If the parent is merged or retired; new places are
                never attached to a place that no longer exists.
            pydantic.ValidationError: If the draft's names break a ``Place``
                invariant.
        """
        _check_parent(draft, parent)
        now = clock.now()
        place = Place(
            id=ids.new_id(),
            code=draft.code,
            level=draft.level,
            parent_id=None if parent is None else parent.id,
            names=draft.names,
            geometry=draft.geometry,
            centroid=draft.centroid,
            created_at=now,
            updated_at=now,
        )
        event = PlaceCreated(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=place.id,
            place_code=place.code,
            version=place.version,
            level=place.level,
            parent_id=place.parent_id,
            names=place.names,
            geometry_type=None
            if place.geometry is None
            else place.geometry.geometry_type,
            centroid=place.centroid,
        )
        return AggregateChange[Place](state=place, events=(event,))


def _check_parent(draft: PlaceDraft, parent: Place | None) -> None:
    parent_code = None if parent is None else parent.code
    if parent_code != draft.parent_code:
        message = (
            f"place {draft.code!r} names parent {draft.parent_code!r} "
            f"but was given {parent_code!r}"
        )
        raise InvalidPlaceHierarchyError(message, details={"code": draft.code})
    parent_level = None if parent is None else parent.level
    if not draft.level.can_be_child_of(parent_level):
        message = (
            f"a {draft.level.value} cannot be placed under "
            f"{'no parent' if parent_level is None else parent_level.value}"
        )
        raise InvalidPlaceHierarchyError(
            message,
            details={
                "code": draft.code,
                "level": draft.level.value,
                "parent_level": None if parent_level is None else parent_level.value,
            },
        )
    if parent is not None and not parent.is_active:
        message = f"parent {parent.code!r} is {parent.status}"
        raise PlaceRetiredError(
            message, details={"code": draft.code, "parent_code": parent.code}
        )
