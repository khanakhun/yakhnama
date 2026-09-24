"""Request bodies, query strings and envelopes of the ingestion HTTP API.

Responses are the application's DTOs as they are: none of them names the user
who requested a run (``RunSummary`` omits ``triggered_by``). Request bodies reuse
the domain's value objects (``DatasetDetails``, ``DatasetVersionDetails``,
``RasterAssetDescription``), so the API and the commands agree on every bound,
the licence invariant included.

Validation messages here are fixed sentences: a rejected variable code, box or
window is never quoted back.

Patterns: API Schema.
"""

from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from yakhnama.modules.ingestion.domain.value_objects import (
    DATASET_CODE_PATTERN,
    SITE_REF_MAX_LENGTH,
    AdapterName,
    VariableCode,
    is_known_variable,
)
from yakhnama.modules.ingestion.public import (
    DatasetDetails,
    DatasetStatus,
    DatasetSummary,
    DatasetVersionDetails,
    ObservationRecord,
    RasterAssetDescription,
    RasterAssetSummary,
    RunSummary,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)
from yakhnama.shared_kernel.value_objects import BoundingBox

DATASET_CODE_MAX_LENGTH: Final = 64
BBOX_MAX_LENGTH: Final = 128
BBOX_EDGES: Final = 4
# Four decimal numbers; WGS84 ranges and edge order are checked by BoundingBox.
BBOX_PATTERN: Final = r"^-?\d{1,3}(\.\d{1,12})?(,-?\d{1,3}(\.\d{1,12})?){3}$"
UNKNOWN_VARIABLE_MESSAGE: Final = "the variable is not registered"
EMPTY_WINDOW_MESSAGE: Final = "the time window must end after it starts"
INVALID_BBOX_MESSAGE: Final = "the bbox is outside WGS84 or its edges are reversed"

DatasetCodeText = Annotated[
    str,
    StringConstraints(max_length=DATASET_CODE_MAX_LENGTH, pattern=DATASET_CODE_PATTERN),
]
"""A dataset's catalog code as a path or query parameter, such as ``pmd_daily``."""

BoundingBoxText = Annotated[
    str, StringConstraints(max_length=BBOX_MAX_LENGTH, pattern=BBOX_PATTERN)
]
Cursor = Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)]
Limit = Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)]


def parse_bounding_box(text: str) -> BoundingBox:
    """Parse ``min_lon,min_lat,max_lon,max_lat`` into a WGS84 box.

    Args:
        text: Four comma-separated numbers, already matched by ``BBOX_PATTERN``.

    Returns:
        The box.

    Raises:
        ValueError: If the numbers are outside WGS84 or the edges are reversed;
            the message never quotes the numbers.
    """
    min_longitude, min_latitude, max_longitude, max_latitude = (
        float(part) for part in text.split(",", BBOX_EDGES - 1)
    )
    try:
        return BoundingBox(
            min_longitude=min_longitude,
            min_latitude=min_latitude,
            max_longitude=max_longitude,
            max_latitude=max_latitude,
        )
    except ValueError as error:
        raise ValueError(INVALID_BBOX_MESSAGE) from error


def _require_window(start: AwareDatetime | None, end: AwareDatetime | None) -> None:
    if start is not None and end is not None and not start < end:
        raise ValueError(EMPTY_WINDOW_MESSAGE)


# --------------------------------------------------------------------------- #
# Catalog                                                                     #
# --------------------------------------------------------------------------- #


class ListDatasetsParameters(BaseModel):
    """Query string of ``GET /api/v1/datasets``.

    Implements: API Schema.

    Attributes:
        status: Only datasets with this status.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DatasetStatus | None = None
    cursor: Cursor = None
    limit: Limit = DEFAULT_PAGE_LIMIT


class DatasetPage(BaseModel):
    """One page of the dataset catalog, ordered by code.

    Implements: API Schema.

    Attributes:
        items: The datasets on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[DatasetSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class ListRunsParameters(BaseModel):
    """Query string of ``GET /api/v1/datasets/{code}/runs``.

    Implements: API Schema.

    Attributes:
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: Cursor = None
    limit: Limit = DEFAULT_PAGE_LIMIT


class RunPage(BaseModel):
    """One page of a dataset's ingestion runs, newest first.

    Implements: API Schema.

    Attributes:
        items: The runs on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[RunSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class RegisterDatasetRequest(BaseModel):
    """Body of ``POST /api/v1/admin/datasets``.

    Implements: API Schema.

    Attributes:
        details: Code, title, publisher, licence (required) and the optional
            descriptions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    details: DatasetDetails


class RecordDatasetVersionRequest(BaseModel):
    """Body of ``POST /api/v1/admin/datasets/{code}/versions``.

    Implements: API Schema.

    Attributes:
        details: Label, retrieval time, input checksum and notes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    details: DatasetVersionDetails


class DatasetStatusChange(StrEnum):
    """The status an administrator may move a dataset to.

    ``active`` is not offered: deprecation and retirement are one-way.

    Implements: API Schema.
    """

    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ChangeDatasetStatusRequest(BaseModel):
    """Body of ``POST /api/v1/admin/datasets/{code}/status``.

    Implements: API Schema.

    Attributes:
        status: ``deprecated`` (no new data, history kept) or ``retired``
            (final).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DatasetStatusChange


class RunIngestionRequest(BaseModel):
    """Body of ``POST /api/v1/admin/datasets/{code}/runs``.

    Implements: API Schema.

    Attributes:
        version_id: The dataset version to ingest; it belongs to the dataset.
        adapter_name: The registered source adapter to read through.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: EntityId
    adapter_name: AdapterName


# --------------------------------------------------------------------------- #
# Observations                                                                #
# --------------------------------------------------------------------------- #


class QueryObservationsParameters(BaseModel):
    """Query string of ``GET /api/v1/observations``.

    The window is half-open, ``from <= observed_at < to`` (**proposed**), so
    consecutive windows never return an observation twice.

    Implements: API Schema.

    Attributes:
        dataset: The dataset's code.
        variable: A registered variable code, for example ``air_temperature``.
        observed_from: ``from``: earliest instant, inclusive, with an offset.
        observed_to: ``to``: latest instant, exclusive, with an offset.
        site_ref: Only this site (``station:<code>`` or ``grid_cell:<id>``).
        version: Only this dataset version's id.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    dataset: DatasetCodeText
    variable: Annotated[VariableCode, StringConstraints(max_length=64)]
    observed_from: AwareDatetime = Field(alias="from")
    observed_to: AwareDatetime = Field(alias="to")
    site_ref: Annotated[
        str | None, Field(min_length=1, max_length=SITE_REF_MAX_LENGTH)
    ] = None
    version: EntityId | None = None
    cursor: Cursor = None
    limit: Limit = DEFAULT_PAGE_LIMIT

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not is_known_variable(self.variable):
            raise ValueError(UNKNOWN_VARIABLE_MESSAGE)
        _require_window(self.observed_from, self.observed_to)
        return self


class ObservationPage(BaseModel):
    """One page of a variable's time series.

    Implements: API Schema.

    Attributes:
        items: The observations, ordered by ``(observed_at, site_ref,
            dataset_version_id)``.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ObservationRecord, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


# --------------------------------------------------------------------------- #
# Rasters                                                                     #
# --------------------------------------------------------------------------- #


class RasterFormat(StrEnum):
    """Representations of the raster listing.

    Implements: API Schema.
    """

    JSON = "json"
    GEOJSON = "geojson"


class ListRasterAssetsParameters(BaseModel):
    """Query string of ``GET /api/v1/raster-assets``.

    Implements: API Schema.

    Attributes:
        bbox: ``min_lon,min_lat,max_lon,max_lat``; only rasters whose footprint
            intersects it.
        acquired_from: ``from``: only rasters acquired at or after this instant.
        acquired_to: ``to``: only rasters acquired before this instant.
        dataset: Only rasters of the dataset with this code.
        output_format: ``format``: ``geojson`` for a STAC ``FeatureCollection``;
            wins over ``Accept``.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    bbox: BoundingBoxText | None = None
    acquired_from: AwareDatetime | None = Field(default=None, alias="from")
    acquired_to: AwareDatetime | None = Field(default=None, alias="to")
    dataset: DatasetCodeText | None = None
    output_format: RasterFormat | None = Field(default=None, alias="format")
    cursor: Cursor = None
    limit: Limit = DEFAULT_PAGE_LIMIT

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.bbox is not None:
            parse_bounding_box(self.bbox)
        _require_window(self.acquired_from, self.acquired_to)
        return self

    def bounding_box(self) -> BoundingBox | None:
        """Return the parsed ``bbox`` filter.

        Returns:
            The box, or ``None`` when no ``bbox`` was sent.
        """
        return None if self.bbox is None else parse_bounding_box(self.bbox)


class RasterAssetPage(BaseModel):
    """One page of catalogued rasters, newest acquisition first.

    Implements: API Schema.

    Attributes:
        items: The rasters on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[RasterAssetSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)


class StacItemCollection(BaseModel):
    """A GeoJSON ``FeatureCollection`` of STAC Items (the ``geojson`` listing).

    Each feature is ``RasterAssetSummary.to_stac_item``, the documented JSON
    boundary for STAC clients.

    Implements: API Schema.

    Attributes:
        type: Always ``FeatureCollection``.
        features: One STAC Item per raster.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: tuple[dict[str, JsonValue], ...] = Field(max_length=MAX_PAGE_LIMIT)


class CatalogueRasterAssetRequest(BaseModel):
    """Body of ``POST /api/v1/admin/raster-assets``.

    Implements: API Schema.

    Attributes:
        dataset: The dataset's code.
        version_id: The version the raster belongs to.
        description: Footprint, acquisition time, platform, bands and assets.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: DatasetCodeText
    version_id: EntityId
    description: RasterAssetDescription
