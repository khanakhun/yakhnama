"""Request and response bodies of the events HTTP API.

Responses are the application's DTOs as they are (``EventSummary``,
``EventDetail``, ``TimelineEntry``): they hold no moderator ids and no reporter
positions (an event's centroid is derived from rounded report points only). This
module adds the query strings, page and timeline envelopes, the GeoJSON
``properties`` of event features and the moderation request bodies.

Hazard-specific ``attributes`` are typed as ``HazardAttributesUnion``, the union of
every schema in the hazards registry, told apart by their ``hazard_type`` member:
a payload that matches no registered schema is refused at parsing (422), and the
command handler refuses attributes of another hazard type than the event's (422,
``AttributesMismatchError``).

Patterns: API Schema.
"""

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from yakhnama.modules.events.domain.factories import MAX_REPORTS_ON_CREATION
from yakhnama.modules.events.domain.value_objects import (
    AffectedPlaceKind,
    ChangeReason,
    EventGeoJson,
    EventTitle,
    RelationNote,
    ReportLinkRole,
)
from yakhnama.modules.events.domain.value_objects import (
    EventSummary as SummaryText,
)
from yakhnama.modules.events.public import (
    AffectedPlace,
    EventGeometry,
    EventPeriod,
    EventStatus,
    EventSummary,
    RelationKind,
    TimelineEntry,
)
from yakhnama.modules.geography.public import PlaceCode
from yakhnama.modules.hazards.public import HazardAttributesUnion, HazardCode
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)
from yakhnama.shared_kernel.value_objects import BoundingBox

CODE_MAX_LENGTH: Final = 64
BBOX_MAX_LENGTH: Final = 128
BBOX_EDGES: Final = 4
TIMELINE_MAX_ENTRIES: Final = 10_000
# Four comma-separated decimal numbers; the WGS84 bounds and the edge order are
# checked by ``BoundingBox`` itself.
BBOX_PATTERN: Final = r"^-?\d{1,3}(\.\d{1,12})?(,-?\d{1,3}(\.\d{1,12})?){3}$"

BoundedHazardCode = Annotated[HazardCode, StringConstraints(max_length=CODE_MAX_LENGTH)]
BoundedPlaceCode = Annotated[PlaceCode, StringConstraints(max_length=CODE_MAX_LENGTH)]
BoundingBoxText = Annotated[
    str, StringConstraints(max_length=BBOX_MAX_LENGTH, pattern=BBOX_PATTERN)
]


def parse_bounding_box(text: str) -> BoundingBox:
    """Parse ``min_lon,min_lat,max_lon,max_lat`` into a WGS84 box.

    Args:
        text: Four comma-separated numbers, already matched by ``BBOX_PATTERN``.

    Returns:
        The box.

    Raises:
        ValueError: If the numbers are outside WGS84 or the edges are reversed
            (``pydantic.ValidationError`` is a ``ValueError``).
    """
    min_longitude, min_latitude, max_longitude, max_latitude = (
        float(part) for part in text.split(",", BBOX_EDGES - 1)
    )
    return BoundingBox(
        min_longitude=min_longitude,
        min_latitude=min_latitude,
        max_longitude=max_longitude,
        max_latitude=max_latitude,
    )


class EventFormat(StrEnum):
    """Representation asked for with ``?format=``; it overrides ``Accept``.

    Implements: API Schema.
    """

    JSON = "json"
    GEOJSON = "geojson"


class ListEventsParameters(BaseModel):
    """Query string of ``GET /api/v1/events``.

    Implements: API Schema.

    Attributes:
        bbox: ``min_lon,min_lat,max_lon,max_lat``; matched against centroids.
        hazard_type: Only events of exactly this hazard type code.
        place_code: Only events listing this place among their affected places.
        status: Only events with this editorial status.
        period_from: Only events that may have lasted until this instant (``from``).
        period_to: Only events that may have started by this instant (``to``).
        verified_only: Only verified events; anyone but a moderator always gets
            published, verified events whatever is sent.
        output_format: ``json`` or ``geojson`` (query name ``format``).
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    bbox: BoundingBoxText | None = None
    hazard_type: BoundedHazardCode | None = None
    place_code: BoundedPlaceCode | None = None
    status: EventStatus | None = None
    period_from: AwareDatetime | None = Field(default=None, alias="from")
    period_to: AwareDatetime | None = Field(default=None, alias="to")
    verified_only: bool | None = None
    output_format: EventFormat | None = Field(default=None, alias="format")
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT

    @model_validator(mode="after")
    def _check_filters(self) -> Self:
        if self.bbox is not None:
            parse_bounding_box(self.bbox)
        if (
            self.period_from is not None
            and self.period_to is not None
            and self.period_from > self.period_to
        ):
            message = "from must not be later than to"
            raise ValueError(message)
        return self

    def bounding_box(self) -> BoundingBox | None:
        """Return the parsed ``bbox`` filter.

        Returns:
            The box, or ``None`` when no ``bbox`` was sent.
        """
        return None if self.bbox is None else parse_bounding_box(self.bbox)


class GetEventParameters(BaseModel):
    """Query string of ``GET /api/v1/events/{event_id}``.

    Implements: API Schema.

    Attributes:
        output_format: ``json`` or ``geojson`` (query name ``format``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    output_format: EventFormat | None = Field(default=None, alias="format")


class EventPage(BaseModel):
    """One page of events, latest start first.

    Implements: API Schema.

    Attributes:
        items: The events on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[EventSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class EventFeatureProperties(BaseModel):
    """GeoJSON ``properties`` of an event in a listing; the geometry is its centroid.

    Implements: API Schema.

    Attributes:
        id: The event.
        hazard_code: Its hazard type code.
        title: Its short name.
        period: When it happened.
        status: Its editorial status.
        verification_state: The state of its verification case, or ``None``.
        place_codes: Codes of every affected place, sorted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    hazard_code: str
    title: str
    period: EventPeriod
    status: EventStatus
    verification_state: str | None
    place_codes: tuple[str, ...]

    @classmethod
    def from_summary(cls, summary: EventSummary) -> Self:
        """Build the properties of an event feature.

        Args:
            summary: The event summary.

        Returns:
            The properties, without the centroid (it is the geometry).
        """
        return cls(
            id=summary.id,
            hazard_code=summary.hazard_code,
            title=summary.title,
            period=summary.period,
            status=summary.status,
            verification_state=summary.verification_state,
            place_codes=summary.place_codes,
        )


class EventTimeline(BaseModel):
    """Body of ``GET /api/v1/events/{event_id}/timeline``.

    Implements: API Schema.

    Attributes:
        event_id: The event.
        entries: Its dated history, in timeline order.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: EntityId
    entries: tuple[TimelineEntry, ...] = Field(max_length=TIMELINE_MAX_ENTRIES)


class CreateEventRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events``.

    Implements: API Schema.

    Attributes:
        report_ids: The distinct reports the event rests on; each becomes a
            ``primary`` link.
        hazard_type: The code of an active hazard type.
        title: Short name, 3 to 200 characters of safe text.
        summary: The moderator's summary, if any.
        attributes: Hazard-specific attributes, with ``hazard_type`` equal to
            ``hazard_type`` above.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_ids: tuple[EntityId, ...] = Field(
        min_length=1, max_length=MAX_REPORTS_ON_CREATION
    )
    hazard_type: BoundedHazardCode
    title: EventTitle
    summary: SummaryText | None = None
    attributes: HazardAttributesUnion | None = None


class LinkReportRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events/{event_id}/reports``.

    Implements: API Schema.

    Attributes:
        report_id: The report to link.
        role: ``primary``, ``supporting`` (the default) or ``contradicting``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId
    role: ReportLinkRole = "supporting"


class ReasonRequest(BaseModel):
    """Body carrying only a reason: unlinking a report or retracting an event.

    Implements: API Schema.

    Attributes:
        reason: Why, as single-line safe text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: ChangeReason


class RelateEventsRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events/{event_id}/relations``.

    Implements: API Schema.

    Attributes:
        to_event_id: The event the relation points to.
        kind: ``triggered_by``, ``part_of`` or ``same_as``.
        note: Why the events are related, if said.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    to_event_id: EntityId
    kind: RelationKind
    note: RelationNote | None = None


class UpdateEventRequest(BaseModel):
    """Body of ``PATCH /api/v1/moderation/events/{event_id}``.

    Only the members sent are changed; ``geometry`` and ``attributes`` may be sent
    as ``null`` to remove them. Each member is applied by its own command, in the
    order geometry, period, attributes.

    Implements: API Schema.

    Attributes:
        geometry: A GeoJSON ``Point``, ``Polygon`` or ``MultiPolygon`` in WGS84.
        period: When the event happened.
        attributes: Hazard-specific attributes of the event's hazard type.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    geometry: EventGeoJson | None = None
    period: EventPeriod | None = None
    attributes: HazardAttributesUnion | None = None

    @model_validator(mode="after")
    def _check_members(self) -> Self:
        if not self.model_fields_set:
            message = "send at least one of geometry, period or attributes"
            raise ValueError(message)
        if "period" in self.model_fields_set and self.period is None:
            message = "period cannot be removed"
            raise ValueError(message)
        # Builds the value object once so bad positions fail at parsing (422).
        self.event_geometry()
        return self

    def event_geometry(self) -> EventGeometry | None:
        """Return the validated geometry, or ``None`` to remove it.

        Returns:
            The geometry value object.
        """
        return None if self.geometry is None else EventGeometry(geojson=self.geometry)


class AddAffectedPlaceRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events/{event_id}/places``.

    Implements: API Schema.

    Attributes:
        place_code: The code of an existing gazetteer place.
        kind: ``origin``, ``impacted`` or ``reference``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_code: BoundedPlaceCode
    kind: AffectedPlaceKind

    def to_affected_place(self) -> AffectedPlace:
        """Build the domain value.

        Returns:
            The affected place.
        """
        return AffectedPlace(place_code=self.place_code, kind=self.kind)


class MergeEventsRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/events/{event_id}/merge``.

    Implements: API Schema.

    Attributes:
        into_event_id: The surviving event.
        reason: Why the events are the same occurrence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    into_event_id: EntityId
    reason: ChangeReason
