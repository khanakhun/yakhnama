"""Request and response bodies of the geography HTTP API.

The JSON detail response is the application's ``PlaceDetail`` DTO as it is. This
module adds the query strings, the page envelope and the GeoJSON ``properties`` of
place features. Geometry in GeoJSON is the place's centroid: boundaries are not part
of the read models until a boundary source is chosen (open question Q1).

Patterns: API Schema.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.geography.application.dto import PlaceSummary
from yakhnama.modules.geography.application.queries import SearchText
from yakhnama.modules.geography.public import AdminLevel, PlaceCode
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)
from yakhnama.shared_kernel.value_objects import LanguageCode

# The bound of a place name's text in the geography domain.
PLACE_NAME_MAX_LENGTH = 200


class PlaceFormat(StrEnum):
    """Representation asked for with ``?format=``; it overrides ``Accept``.

    Implements: API Schema.
    """

    JSON = "json"
    GEOJSON = "geojson"


class ListPlacesParameters(BaseModel):
    """Query string of ``GET /api/v1/places``.

    Implements: API Schema.

    Attributes:
        q: Search text, 1 to 100 characters after stripping.
        language: Match only names in this language and show names in it.
        level: Only list places at this administrative level.
        include_inactive: Also list merged and retired places.
        output_format: ``json`` or ``geojson`` (query name ``format``).
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    q: SearchText
    language: LanguageCode | None = None
    level: AdminLevel | None = None
    include_inactive: bool = False
    output_format: PlaceFormat | None = Field(default=None, alias="format")
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class GetPlaceParameters(BaseModel):
    """Query string of ``GET /api/v1/places/{place_id}``.

    Implements: API Schema.

    Attributes:
        output_format: ``json`` or ``geojson`` (query name ``format``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    output_format: PlaceFormat | None = Field(default=None, alias="format")


class PlacePage(BaseModel):
    """One page of places matching a search.

    Implements: API Schema.

    Attributes:
        items: The places on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[PlaceSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class PlaceFeatureProperties(BaseModel):
    """GeoJSON ``properties`` of a place in a search result.

    Implements: API Schema.

    Attributes:
        id: The place id.
        code: Stable machine code.
        level: Administrative level.
        display_name: The text of the name chosen for the requested language.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    code: PlaceCode
    level: AdminLevel
    display_name: str = Field(min_length=1, max_length=PLACE_NAME_MAX_LENGTH)

    @classmethod
    def from_summary(cls, summary: PlaceSummary) -> "PlaceFeatureProperties":
        """Build the properties of a search result.

        Args:
            summary: The place summary.

        Returns:
            Its GeoJSON properties.
        """
        return cls(
            id=summary.id,
            code=summary.code,
            level=summary.level,
            display_name=summary.name.text,
        )
