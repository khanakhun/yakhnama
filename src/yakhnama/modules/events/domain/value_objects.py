"""Value objects of the ``events`` bounded context.

An event is the canonical record of one real-world hazard occurrence. Its geometry, its
period, the places it touched, its links to the reports it rests on and its relations
to other events are values defined here. Choices marked **proposed** are defaults, not
domain facts; each is listed in ``docs/data-dictionary/events.md``.

Patterns: Value Object.
"""

import math
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal, Self

from geojson_pydantic import MultiPolygon, Point, Polygon
from geojson_pydantic.types import Position
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.modules.geography.public import PlaceCode
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

TITLE_MIN_LENGTH: Final = 3
TITLE_MAX_LENGTH: Final = 200
SUMMARY_MAX_LENGTH: Final = 2000
RELATION_NOTE_MAX_LENGTH: Final = 500
CHANGE_REASON_MAX_LENGTH: Final = 1000

EventTitle = Annotated[str, *safe_text(TITLE_MAX_LENGTH, TITLE_MIN_LENGTH)]
"""A short, single-line name of the event: safe text of 3 to 200 characters."""

EventSummary = Annotated[str, *safe_text(SUMMARY_MAX_LENGTH, allow_line_breaks=True)]
"""A moderator's summary: safe text of 1 to 2000 characters; line breaks allowed."""

RelationNote = Annotated[
    str, *safe_text(RELATION_NOTE_MAX_LENGTH, allow_line_breaks=True)
]
"""Why two events are related: safe text of 1 to 500 characters."""

ChangeReason = Annotated[
    str, *safe_text(CHANGE_REASON_MAX_LENGTH, allow_line_breaks=True)
]
"""Why a report was unlinked or an event retracted or merged: 1 to 1000 characters."""


def _normalise_to_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


# --------------------------------------------------------------------------- #
# Status                                                                      #
# --------------------------------------------------------------------------- #


class EventStatus(StrEnum):
    """Editorial status of an event record (**proposed** set).

    This is not the verification state: whether an event is verified is decided in
    the ``verification`` module and mirrored as ``verification_state`` on the read
    model. ``retracted`` and ``merged`` are final; nothing is ever deleted.

    Implements: Value Object.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    RETRACTED = "retracted"
    MERGED = "merged"

    @property
    def is_final(self) -> bool:
        """Return ``True`` for ``retracted`` and ``merged``, which accept no change."""
        return self in _FINAL_STATUSES


_FINAL_STATUSES: Final = frozenset({EventStatus.RETRACTED, EventStatus.MERGED})


# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #

EventGeoJson = Annotated[Point | Polygon | MultiPolygon, Field(discriminator="type")]
"""The GeoJSON geometry types an event footprint may take."""

GeometryType = Literal["Point", "Polygon", "MultiPolygon"]

_TWO_DIMENSIONS: Final = 2
_MAX_ABS_LONGITUDE: Final = 180.0
_MAX_ABS_LATITUDE: Final = 90.0
# The same bound as a place footprint: large enough for a detailed flood extent, small
# enough that one request cannot exhaust memory.
_MAX_POSITIONS: Final = 1_000_000


class EventGeometry(BaseModel):
    """Where an event happened, in WGS84 (EPSG:4326), longitude first.

    A point for a located observation, a polygon or multipolygon for a mapped extent
    (a flood footprint, a landslide scar). ``geojson-pydantic`` checks the GeoJSON
    structure; this wrapper adds finite, two-dimensional, in-bounds positions and a
    non-empty geometry, as ``PlaceGeometry`` does in ``geography``. The wrapped
    geometry is copied on the way in, because ``geojson-pydantic`` models are mutable.

    Implements: Value Object.

    Attributes:
        geojson: The geometry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    SRID: ClassVar[int] = 4326

    geojson: EventGeoJson

    @field_validator("geojson", mode="before")
    @classmethod
    def _copy_geometry(cls, value: object) -> object:
        if isinstance(value, Point | Polygon | MultiPolygon):
            return value.model_dump()
        return value

    @field_validator("geojson", mode="after")
    @classmethod
    def _check_positions(
        cls, geojson: Point | Polygon | MultiPolygon
    ) -> Point | Polygon | MultiPolygon:
        count = 0
        for position in _positions(geojson):
            count += 1
            _check_position(position)
        if count == 0:
            message = "geometry must contain at least one position"
            raise ValueError(message)
        if count > _MAX_POSITIONS:
            message = f"geometry has more than {_MAX_POSITIONS} positions"
            raise ValueError(message)
        return geojson

    @property
    def geometry_type(self) -> GeometryType:
        """Return the GeoJSON type name: ``Point``, ``Polygon`` or ``MultiPolygon``."""
        return self.geojson.type

    def bounding_box(self) -> BoundingBox:
        """Return the smallest WGS84 box containing every position.

        Returns:
            The tight bounding box; for a point, a box of zero size.
        """
        return BoundingBox.from_coordinates(
            _to_coordinates(position) for position in _positions(self.geojson)
        )

    def centroid(self) -> Coordinates:
        """Return a representative point of the geometry, computed without Shapely.

        For a point, the point itself. For a polygon or multipolygon, the arithmetic
        mean of the exterior-ring vertices (the closing vertex counted once, holes
        ignored). This is a **proposed** approximation: it is not the area centroid,
        it is biased towards densely digitised edges and it can fall outside a
        concave shape, but it is deterministic, always inside the bounding box of
        the exterior rings, and good enough for map pins and bounding-box search.
        PostGIS ``ST_Centroid`` may be computed alongside in infrastructure.

        Returns:
            The representative point.
        """
        if isinstance(self.geojson, Point):
            return _to_coordinates(self.geojson.coordinates)
        return mean_coordinates(
            _to_coordinates(position) for position in _exterior_vertices(self.geojson)
        )

    @classmethod
    def from_coordinates(cls, coordinates: Coordinates) -> Self:
        """Wrap a point.

        Args:
            coordinates: The WGS84 position.

        Returns:
            A point geometry.
        """
        return cls(geojson=coordinates.to_geojson_point())


def mean_coordinates(points: Iterable[Coordinates]) -> Coordinates:
    """Return the arithmetic mean of ``points``, kept inside their bounding box.

    ``math.fsum`` keeps the sums exact enough, and the result is clamped to the
    bounding box because a floating-point mean of equal values can land one unit in
    the last place outside them. Means are taken in degrees, which is adequate for a
    region the size of Gilgit-Baltistan and wrong across the antimeridian, which the
    kernel's ``BoundingBox`` does not support either.

    Args:
        points: At least one point.

    Returns:
        The mean point.

    Raises:
        ValidationError: If ``points`` is empty (from ``BoundingBox``).
    """
    collected = list(points)
    box = BoundingBox.from_coordinates(collected)
    longitude = math.fsum(point.longitude for point in collected) / len(collected)
    latitude = math.fsum(point.latitude for point in collected) / len(collected)
    return Coordinates(
        longitude=min(max(longitude, box.min_longitude), box.max_longitude),
        latitude=min(max(latitude, box.min_latitude), box.max_latitude),
    )


def _to_coordinates(position: Position) -> Coordinates:
    return Coordinates(longitude=position.longitude, latitude=position.latitude)


def _polygons(
    geojson: Polygon | MultiPolygon,
) -> Sequence[Sequence[Sequence[Position]]]:
    # A Polygon is a list of rings; a MultiPolygon a list of such lists.
    return (
        [geojson.coordinates] if isinstance(geojson, Polygon) else geojson.coordinates
    )


def _positions(geojson: Point | Polygon | MultiPolygon) -> Iterator[Position]:
    if isinstance(geojson, Point):
        yield geojson.coordinates
        return
    for polygon in _polygons(geojson):
        for ring in polygon:
            yield from ring


def _exterior_vertices(geojson: Polygon | MultiPolygon) -> Iterator[Position]:
    for polygon in _polygons(geojson):
        if polygon:
            # GeoJSON rings repeat the first position at the end; counting it twice
            # would pull the mean towards the first vertex.
            yield from polygon[0][:-1]


def _check_position(position: Position) -> None:
    if len(position) != _TWO_DIMENSIONS:
        message = "positions must be two-dimensional (longitude, latitude)"
        raise ValueError(message)
    longitude, latitude = position.longitude, position.latitude
    if not (math.isfinite(longitude) and abs(longitude) <= _MAX_ABS_LONGITUDE):
        message = f"longitude {longitude} is outside WGS84 bounds [-180, 180]"
        raise ValueError(message)
    if not (math.isfinite(latitude) and abs(latitude) <= _MAX_ABS_LATITUDE):
        message = f"latitude {latitude} is outside WGS84 bounds [-90, 90]"
        raise ValueError(message)


# --------------------------------------------------------------------------- #
# Period                                                                      #
# --------------------------------------------------------------------------- #

_LATEST_INSTANT: Final = datetime.max.replace(tzinfo=UTC)
_ONE_MICROSECOND: Final = timedelta(microseconds=1)
_MONTHS_PER_YEAR: Final = 12
_MONTHS_PER_SEASON: Final = 3


class EventPeriod(BaseModel):
    """When an event happened: a start and an optional end, each with its precision.

    ``ended_at`` is ``None`` when the end is unknown or the event was a single moment.
    The order check is precision-aware (a **proposed** rule): the last instant of the
    end's precision period must not be earlier than the truncated start. A start of
    2024-07-15 (day) with an end of 2024-07 (month) is accepted, because the end may
    fall on any later day of July; an end of 2024-06 (month) is rejected.

    Implements: Value Object.

    Attributes:
        started_at: When the event started, with precision.
        ended_at: When it ended, with precision, or ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    started_at: DateWithPrecision
    ended_at: DateWithPrecision | None = None

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if (
            self.ended_at is not None
            and latest_instant_of(self.ended_at) < self.started_at.truncate().value
        ):
            message = "ended_at must not be earlier than started_at"
            raise ValueError(message)
        return self

    def earliest_instant(self) -> datetime:
        """Return the earliest instant the event may have started.

        Returns:
            ``started_at`` floored to the start of its precision period, UTC.
        """
        return self.started_at.truncate().value

    def latest_instant(self) -> datetime:
        """Return the latest instant the event may have lasted to, inclusive.

        Without ``ended_at`` the event is treated as lasting only for the period of
        ``started_at`` (a **proposed** reading of an unknown end): an undated end
        must not make an old event match every later search.

        Returns:
            The last microsecond of the precision period of ``ended_at`` (or of
            ``started_at``); for ``exact`` the instant itself.
        """
        return latest_instant_of(self.ended_at or self.started_at)

    def overlaps(self, start: datetime | None, end: datetime | None) -> bool:
        """Tell whether the event may have happened within ``[start, end]``.

        Args:
            start: Lower bound, inclusive; ``None`` for no lower bound.
            end: Upper bound, inclusive; ``None`` for no upper bound.

        Returns:
            ``True`` if ``[earliest_instant, latest_instant]`` meets the window.
        """
        return (end is None or self.earliest_instant() <= end) and (
            start is None or self.latest_instant() >= start
        )


def latest_instant_of(moment: DateWithPrecision) -> datetime:
    """Return the last instant of the precision period ``moment`` falls in.

    Args:
        moment: An instant with its precision.

    Returns:
        The last microsecond of that hour, day, month, season or year, UTC; the
        instant itself for ``exact``; ``datetime.max`` if the period runs past it.
    """
    start = moment.truncate().value
    if moment.precision is DatePrecision.EXACT:
        return start
    try:
        return _next_period_start(start, moment.precision) - _ONE_MICROSECOND
    except (OverflowError, ValueError):
        # Only reachable in year 9999, where the next period is unrepresentable.
        return _LATEST_INSTANT


def _next_period_start(start: datetime, precision: DatePrecision) -> datetime:
    if precision is DatePrecision.HOUR:
        return start + timedelta(hours=1)
    if precision is DatePrecision.DAY:
        return start + timedelta(days=1)
    if precision is DatePrecision.YEAR:
        return start.replace(year=start.year + 1)
    months = _MONTHS_PER_SEASON if precision is DatePrecision.SEASON else 1
    month_index = start.month - 1 + months
    return start.replace(
        year=start.year + month_index // _MONTHS_PER_YEAR,
        month=month_index % _MONTHS_PER_YEAR + 1,
    )


# --------------------------------------------------------------------------- #
# Places, reports and relations                                               #
# --------------------------------------------------------------------------- #

AffectedPlaceKind = Literal["origin", "impacted", "reference"]
"""How a place relates to an event (**proposed**): where it started, where it did
harm, or a named reference point used to locate it."""


class AffectedPlace(BaseModel):
    """A place from the ``geography`` gazetteer that an event concerns.

    Implements: Value Object.

    Attributes:
        place_code: Stable code of the place, for example ``pk.gb.hunza``.
        kind: ``origin``, ``impacted`` or ``reference``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_code: PlaceCode
    kind: AffectedPlaceKind


ReportLinkRole = Literal["primary", "supporting", "contradicting"]
"""What a linked report contributes (**proposed**): it is a direct observation the
event rests on, it adds detail, or it contradicts the record."""


class ReportLink(BaseModel):
    """The link between an event and one report it is based on or informed by.

    Implements: Value Object.

    Attributes:
        report_id: The linked report.
        linked_by: Who made the link.
        linked_at: When, UTC.
        role: ``primary``, ``supporting`` or ``contradicting``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId
    linked_by: EntityId
    linked_at: AwareDatetime
    role: ReportLinkRole

    _utc = field_validator("linked_at", mode="after")(_normalise_to_utc)


class RelationKind(StrEnum):
    """How one event relates to another.

    - ``triggered_by``: ``from`` was caused by ``to`` (a debris flow triggered by a
      cloudburst). Directed.
    - ``part_of``: ``from`` is a component of ``to`` (one flood wave of a monsoon
      flood). Directed; never cyclic.
    - ``same_as``: both records describe one occurrence and should be merged.
      Symmetric: ``a same_as b`` also means ``b same_as a``.

    Implements: Value Object.
    """

    TRIGGERED_BY = "triggered_by"
    PART_OF = "part_of"
    SAME_AS = "same_as"


class EventRelation(BaseModel):
    """A directed, typed link between two different events.

    Implements: Value Object.

    Attributes:
        from_event_id: The event the relation starts from.
        to_event_id: The event it points to; never ``from_event_id``.
        kind: ``triggered_by``, ``part_of`` or ``same_as``.
        note: Optional explanation.
        related_by: Who recorded the relation.
        related_at: When, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_event_id: EntityId
    to_event_id: EntityId
    kind: RelationKind
    note: RelationNote | None = None
    related_by: EntityId
    related_at: AwareDatetime

    _utc = field_validator("related_at", mode="after")(_normalise_to_utc)

    @model_validator(mode="after")
    def _check_distinct(self) -> Self:
        if self.from_event_id == self.to_event_id:
            message = "an event cannot be related to itself"
            raise ValueError(message)
        return self

    @property
    def key(self) -> tuple[EntityId, EntityId, RelationKind]:
        """Return the identity of the relation; ``same_as`` pairs are unordered."""
        if self.kind is RelationKind.SAME_AS:
            low, high = sorted((self.from_event_id, self.to_event_id))
            return (low, high, self.kind)
        return (self.from_event_id, self.to_event_id, self.kind)

    def involves(self, event_id: EntityId) -> bool:
        """Tell whether ``event_id`` is either end of the relation.

        Args:
            event_id: An event.

        Returns:
            ``True`` if the relation starts or ends at ``event_id``.
        """
        return event_id in (self.from_event_id, self.to_event_id)

    def other_end(self, event_id: EntityId) -> EntityId:
        """Return the event at the opposite end from ``event_id``.

        Args:
            event_id: One end of the relation.

        Returns:
            The other end; ``to_event_id`` if ``event_id`` is not an end at all.
        """
        return self.from_event_id if event_id == self.to_event_id else self.to_event_id


class ReportUnlink(BaseModel):
    """The record of a report link that was removed, kept so nothing is lost.

    Implements: Value Object.

    Attributes:
        report_id: The report that was unlinked.
        role: The role it had while linked.
        unlinked_by: Who removed the link.
        unlinked_at: When, UTC.
        reason: Why.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId
    role: ReportLinkRole
    unlinked_by: EntityId
    unlinked_at: AwareDatetime
    reason: ChangeReason

    _utc = field_validator("unlinked_at", mode="after")(_normalise_to_utc)


class ReportForEvent(BaseModel):
    """What the events domain needs to know about a report, and nothing more.

    The application maps a report from the ``reports`` facade to this value, so the
    events domain never depends on the reports module. ``coordinates`` must be the
    report's **public, rounded** point: an event's centroid is published, and a
    reporter's exact GPS position must never leak through it (Phase 2 security
    review).

    Implements: Value Object.

    Attributes:
        id: The report.
        observed_at: When the reporter observed the hazard, with precision.
        coordinates: The report's public point.
        source_id: The provenance record of the report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    observed_at: DateWithPrecision
    coordinates: Coordinates
    source_id: EntityId
