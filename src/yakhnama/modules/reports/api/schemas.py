"""Request and response bodies of the reports HTTP API.

Responses are the application's DTOs as they are: ``ReportDetail`` (exact or
rounded as the caller may see it, with ``coordinates_are_exact``) and
``ReportSummary`` (always rounded, never an accuracy). This module adds the request
bodies, the query string of the listing, its page envelope and the GeoJSON
``properties`` of a report feature. Request bodies reuse the domain's constrained
types (safe text, UUIDv7 ids, WGS84 coordinates, codes), so the API and the
commands agree on every bound and the commands validate again when built.

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

from yakhnama.modules.reports.domain.value_objects import (
    GPS_ACCURACY_MAX_METRES,
    MEDIA_PER_REPORT_MAX,
    ClientReportId,
    Description,
    GpsAccuracy,
    GuessedHazardCode,
    PlaceHint,
    WithdrawalReason,
)
from yakhnama.modules.reports.public import (
    HazardGuess,
    ObservationPoint,
    ReportContent,
    ReportStatus,
    ReportSummary,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Coordinates,
    DateWithPrecision,
    LanguageCode,
)

CODE_MAX_LENGTH: Final = 64
BBOX_MAX_LENGTH: Final = 128
BBOX_EDGES: Final = 4
# Four comma-separated decimal numbers; the WGS84 bounds and the edge order are
# checked by ``BoundingBox`` itself.
BBOX_PATTERN: Final = r"^-?\d{1,3}(\.\d{1,12})?(,-?\d{1,3}(\.\d{1,12})?){3}$"

BoundedHazardCode = Annotated[
    GuessedHazardCode, StringConstraints(max_length=CODE_MAX_LENGTH)
]
BoundedPlaceHint = Annotated[PlaceHint, StringConstraints(max_length=CODE_MAX_LENGTH)]
BoundingBoxText = Annotated[
    str, StringConstraints(max_length=BBOX_MAX_LENGTH, pattern=BBOX_PATTERN)
]
AccuracyMetres = Annotated[
    float, Field(ge=0.0, le=GPS_ACCURACY_MAX_METRES, allow_inf_nan=False)
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


class ReportFormat(StrEnum):
    """Representation asked for with ``?format=``; it overrides ``Accept``.

    Implements: API Schema.
    """

    JSON = "json"
    GEOJSON = "geojson"


class ReportContentRequest(BaseModel):
    """What the reporter observed, as a request body carries it.

    Implements: API Schema.

    Attributes:
        observed_at: When it was observed, with an explicit offset and precision.
        coordinates: Where the reporter was, WGS84, longitude first.
        accuracy_metres: The device's accuracy radius in metres, if known.
        description: What they saw, 1 to 4000 characters of safe text.
        original_language: The language and script of ``description``.
        hazard_guess: What they think it was, with their own confidence.
        place_hint: The code of a place they picked, if any.
        media_ids: Up to 20 of their own uploaded media assets.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_at: DateWithPrecision
    coordinates: Coordinates
    accuracy_metres: AccuracyMetres | None = None
    description: Description
    original_language: LanguageCode
    hazard_guess: HazardGuess | None = None
    place_hint: BoundedPlaceHint | None = None
    media_ids: tuple[EntityId, ...] = Field(default=(), max_length=MEDIA_PER_REPORT_MAX)

    def to_content(self) -> ReportContent:
        """Build the domain content of the report.

        Returns:
            The content value object.

        Raises:
            pydantic.ValidationError: If the content breaks a domain rule (for
                example repeated media ids); rendered as 422.
        """
        return ReportContent(
            observed_at=self.observed_at,
            observation=ObservationPoint(
                coordinates=self.coordinates,
                accuracy=(
                    None
                    if self.accuracy_metres is None
                    else GpsAccuracy.metres(self.accuracy_metres)
                ),
            ),
            description=self.description,
            original_language=self.original_language,
            hazard_guess=self.hazard_guess,
            place_hint=self.place_hint,
            media_ids=self.media_ids,
        )


class SubmitReportRequest(ReportContentRequest):
    """Body of ``POST /api/v1/reports``.

    Implements: API Schema.

    Attributes:
        client_report_id: The UUIDv7 the client generated; it becomes the report id
            and makes a retried submission return the same report.
        organization_id: The organisation reported for, if any; the reporter must
            be a member.
    """

    client_report_id: ClientReportId
    organization_id: EntityId | None = None


class ReviseReportRequest(ReportContentRequest):
    """Body of ``POST /api/v1/reports/{report_id}/revisions``: the full content.

    Implements: API Schema.
    """


class WithdrawReportRequest(BaseModel):
    """Body of ``POST /api/v1/reports/{report_id}/withdrawal``.

    Implements: API Schema.

    Attributes:
        reason: Why, 1 to 500 characters of single-line safe text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: WithdrawalReason


class ListReportsParameters(BaseModel):
    """Query string of ``GET /api/v1/reports``.

    Implements: API Schema.

    Attributes:
        status: Only reports in this status.
        hazard_code: Only reports whose reporter guessed this hazard type.
        bbox: ``min_lon,min_lat,max_lon,max_lat``; matched against the *rounded*
            positions.
        observed_from: Only reports observed at or after this instant (``from``).
        observed_to: Only reports observed at or before this instant (``to``).
        output_format: ``json`` or ``geojson`` (query name ``format``).
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    status: ReportStatus | None = None
    hazard_code: BoundedHazardCode | None = None
    bbox: BoundingBoxText | None = None
    observed_from: AwareDatetime | None = Field(default=None, alias="from")
    observed_to: AwareDatetime | None = Field(default=None, alias="to")
    output_format: ReportFormat | None = Field(default=None, alias="format")
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT

    @model_validator(mode="after")
    def _check_filters(self) -> Self:
        if self.bbox is not None:
            parse_bounding_box(self.bbox)
        if (
            self.observed_from is not None
            and self.observed_to is not None
            and self.observed_from > self.observed_to
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


class ReportPage(BaseModel):
    """One page of reports, newest first, with rounded positions.

    Implements: API Schema.

    Attributes:
        items: The reports on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReportSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class ReportFeatureProperties(BaseModel):
    """GeoJSON ``properties`` of a report feature; the geometry is rounded.

    Implements: API Schema.

    Attributes:
        id: The report.
        status: Lifecycle status.
        revision: Position in the revision chain.
        observed_at: When it was observed.
        hazard_code: The reporter's hazard guess, if any.
        place_hint: The picked place, if any.
        media_count: How many media assets are attached.
        submitted_at: When it was submitted, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    status: ReportStatus
    revision: int
    observed_at: DateWithPrecision
    hazard_code: str | None
    place_hint: str | None
    media_count: int
    submitted_at: AwareDatetime | None

    @classmethod
    def from_summary(cls, summary: ReportSummary) -> Self:
        """Build the properties of a report feature.

        Args:
            summary: The rounded summary.

        Returns:
            The properties, without the position (it is the geometry).
        """
        return cls(
            id=summary.id,
            status=summary.status,
            revision=summary.revision,
            observed_at=summary.observed_at,
            hazard_code=summary.hazard_code,
            place_hint=summary.place_hint,
            media_count=summary.media_count,
            submitted_at=summary.submitted_at,
        )
