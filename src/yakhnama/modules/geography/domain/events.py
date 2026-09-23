"""Domain events of the ``geography`` bounded context.

Every event carries the place's id as ``aggregate_id``, ``"place"`` as
``aggregate_type`` and the place ``version`` *after* the change, so an audit log can
order and check changes without reading the aggregate. No event carries personal data:
place names, codes and geometry are public reference data.

Patterns: Domain Events.
"""

from typing import ClassVar, Final, Literal

from pydantic import Field

from yakhnama.modules.geography.domain.value_objects import (
    AdminLevel,
    GeometryType,
    PlaceCode,
    PlaceName,
    PlaceNameText,
    PlaceVersion,
    ScriptCode,
    StatusReason,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    LanguageCode,
)

PLACE_AGGREGATE_TYPE: Final = "place"


class PlaceEvent(DomainEvent):
    """Fields shared by every event about a place; never published on its own.

    It declares no ``event_type``, so instantiating it raises ``TypeError``.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"place"``.
        place_code: Stable code of the place, so a log line is readable on its own.
        version: The place's version after the change.
    """

    aggregate_type: Literal["place"] = PLACE_AGGREGATE_TYPE
    place_code: PlaceCode
    version: PlaceVersion


class PlaceCreated(PlaceEvent):
    """A place was recorded for the first time.

    Implements: Domain Events.

    Attributes:
        level: Administrative level.
        parent_id: The enclosing place, ``None`` for a country.
        names: Every name the place was created with.
        geometry_type: Type of the initial geometry, or ``None`` without one.
        centroid: The initial centroid, or ``None``.
    """

    event_type: ClassVar[str] = "geography.place_created"

    level: AdminLevel
    parent_id: EntityId | None
    names: tuple[PlaceName, ...] = Field(min_length=1, max_length=64)
    geometry_type: GeometryType | None
    centroid: Coordinates | None


class PlaceNameAdded(PlaceEvent):
    """A name was added to a place.

    Implements: Domain Events.

    Attributes:
        name: The name as stored (after any demotion rule in ``Place.add_name``).
    """

    event_type: ClassVar[str] = "geography.place_name_added"

    name: PlaceName


class PlacePreferredNameChanged(PlaceEvent):
    """The preferred name of a place in one language changed.

    Implements: Domain Events.

    Attributes:
        language: The language whose preferred name changed.
        previous_text: The previously preferred name, or ``None`` if there was none.
        text: The newly preferred name.
        script: Script of the newly preferred name, or ``None`` if not recorded.
    """

    event_type: ClassVar[str] = "geography.place_preferred_name_changed"

    language: LanguageCode
    previous_text: PlaceNameText | None
    text: PlaceNameText
    script: ScriptCode | None


class PlaceGeometryChanged(PlaceEvent):
    """The geometry of a place was set, replaced or removed.

    The full geometry is not copied into the event, as a boundary can hold many
    thousands of positions; the type and bounding box are enough to audit the change
    and the geometry itself is in the aggregate.

    Implements: Domain Events.

    Attributes:
        previous_geometry_type: Type before the change, ``None`` if there was none.
        geometry_type: Type after the change, ``None`` if it was removed.
        bounding_box: Bounding box after the change, ``None`` if it was removed.
    """

    event_type: ClassVar[str] = "geography.place_geometry_changed"

    previous_geometry_type: GeometryType | None
    geometry_type: GeometryType | None
    bounding_box: BoundingBox | None


class PlaceCentroidChanged(PlaceEvent):
    """The centroid of a place was set, replaced or removed.

    Implements: Domain Events.

    Attributes:
        previous_centroid: Centroid before the change, ``None`` if there was none.
        centroid: Centroid after the change, ``None`` if it was removed.
    """

    event_type: ClassVar[str] = "geography.place_centroid_changed"

    previous_centroid: Coordinates | None
    centroid: Coordinates | None


class PlaceRetired(PlaceEvent):
    """A place was retired: it no longer exists as an administrative unit.

    Implements: Domain Events.

    Attributes:
        reason: Why it was retired, 1 to 500 characters.
    """

    event_type: ClassVar[str] = "geography.place_retired"

    reason: StatusReason


class PlaceMerged(PlaceEvent):
    """A place was merged into another place, which replaces it.

    Implements: Domain Events.

    Attributes:
        target_id: The place that replaces this one.
        reason: Why it was merged, 1 to 500 characters.
    """

    event_type: ClassVar[str] = "geography.place_merged"

    target_id: EntityId
    reason: StatusReason
