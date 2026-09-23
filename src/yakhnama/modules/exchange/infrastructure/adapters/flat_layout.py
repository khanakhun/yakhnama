"""The flat, typed column layout shared by the tabular export formats.

CSV and GeoParquet write one row per exported record with one scalar per column.
This module is the single place that decides those columns, their order and their
types for each dataset, so both formats publish the same layout and a new field of
an export row is noticed by one test (every model field must map to a column).

Rules (**proposed**, documented in ``docs/architecture/exchange.md``):

- A ``DateWithPrecision`` becomes two columns, ``<field>`` (UTC timestamp) and
  ``<field>_precision``.
- ``Coordinates`` become ``<prefix>longitude`` and ``<prefix>latitude``.
- A tuple of codes or ids becomes one list column (CSV joins it with ``;``, the
  backfill separator; GeoParquet writes ``list<string>``).
- A claim value becomes typed columns, one per variant (``count``,
  ``measurement_value`` and ``measurement_unit``, ``amount``, ``currency`` and
  ``price_year``) beside ``value_kind``; the columns of the other variants are
  empty. ``amount`` is decimal text, so money stays exact.
- A claim scope becomes ``scope_place_code`` and ``scope_asset_id``.
- The geometry (an event's geometry, else its centroid point; a report's rounded
  point; none for claims) is not a flat column: each format writes it natively.

Patterns: Value Object.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from geojson_pydantic import MultiPolygon, Point, Polygon
from pydantic import BaseModel, ConfigDict

from yakhnama.modules.exchange.application.formats import (
    ClaimExportRow,
    EventExportRow,
    ExportRow,
    ReportExportRow,
)
from yakhnama.modules.exchange.domain.value_objects import ExportDataset
from yakhnama.modules.impacts.public import CountValue, MeasurementValue
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.value_objects import Coordinates, DateWithPrecision

type FlatValue = str | int | float | Decimal | datetime | tuple[str, ...] | None
"""One cell before a format serialises it; ``None`` is an absent value."""

type ExportGeometry = Point | Polygon | MultiPolygon
"""The GeoJSON geometry a spatial format writes for one row."""


class ColumnKind(StrEnum):
    """The type of one flat column.

    Implements: Value Object.
    """

    TEXT = "text"
    TEXT_LIST = "text_list"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    TIMESTAMP = "timestamp"


class FlatColumn(BaseModel):
    """One column of a dataset's flat layout.

    Implements: Value Object.

    Attributes:
        name: The column name, unique within the layout.
        kind: Its type.
        field: The export row field it comes from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: ColumnKind
    field: str


class DatasetLayout(BaseModel):
    """The flat columns of one dataset and where its geometry comes from.

    Implements: Value Object.

    Attributes:
        dataset: The dataset.
        columns: The flat columns in output order.
        geometry_fields: The row fields the geometry is derived from; empty for a
            dataset without geometry.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: ExportDataset
    columns: tuple[FlatColumn, ...]
    geometry_fields: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Return the column names in output order."""
        return tuple(column.name for column in self.columns)

    @property
    def has_geometry(self) -> bool:
        """Tell whether rows of this dataset can carry a geometry."""
        return bool(self.geometry_fields)


def _column(name: str, kind: ColumnKind, field: str | None = None) -> FlatColumn:
    return FlatColumn(name=name, kind=kind, field=field or name)


def _moment_columns(field: str) -> tuple[FlatColumn, ...]:
    return (
        _column(field, ColumnKind.TIMESTAMP),
        _column(f"{field}_precision", ColumnKind.TEXT, field),
    )


def _point_columns(field: str, prefix: str) -> tuple[FlatColumn, ...]:
    return (
        _column(f"{prefix}longitude", ColumnKind.FLOAT, field),
        _column(f"{prefix}latitude", ColumnKind.FLOAT, field),
    )


_TEXT: Final = ColumnKind.TEXT

EVENTS_LAYOUT: Final = DatasetLayout(
    dataset=ExportDataset.EVENTS,
    columns=(
        _column("event_id", _TEXT),
        _column("hazard_code", _TEXT),
        _column("title", _TEXT),
        _column("summary", _TEXT),
        *_moment_columns("started_at"),
        *_moment_columns("ended_at"),
        *_point_columns("centroid", "centroid_"),
        _column("place_codes", ColumnKind.TEXT_LIST),
        _column("source_ids", ColumnKind.TEXT_LIST),
        _column("status", _TEXT),
        _column("verification_state", _TEXT),
        _column("updated_at", ColumnKind.TIMESTAMP),
    ),
    geometry_fields=("geometry", "centroid"),
)
"""Flat layout of the ``events`` dataset."""

CLAIMS_LAYOUT: Final = DatasetLayout(
    dataset=ExportDataset.CLAIMS,
    columns=(
        _column("claim_id", _TEXT),
        _column("event_id", _TEXT),
        _column("metric_code", _TEXT),
        _column("value_kind", _TEXT, "value"),
        _column("count", ColumnKind.INTEGER, "value"),
        _column("measurement_value", ColumnKind.FLOAT, "value"),
        _column("measurement_unit", _TEXT, "value"),
        _column("amount", ColumnKind.DECIMAL, "value"),
        _column("currency", _TEXT, "value"),
        _column("price_year", ColumnKind.INTEGER, "value"),
        _column("confidence", _TEXT),
        _column("source_id", _TEXT),
        _column("source_type", _TEXT),
        *_moment_columns("claimed_at"),
        _column("scope_place_code", _TEXT, "scope"),
        _column("scope_asset_id", _TEXT, "scope"),
        _column("status", _TEXT),
        _column("supersedes_id", _TEXT),
        _column("created_at", ColumnKind.TIMESTAMP),
    ),
    geometry_fields=(),
)
"""Flat layout of the ``claims`` dataset; claims carry no geometry."""

REPORTS_LAYOUT: Final = DatasetLayout(
    dataset=ExportDataset.REPORTS,
    columns=(
        _column("report_id", _TEXT),
        _column("status", _TEXT),
        _column("revision", ColumnKind.INTEGER),
        *_moment_columns("observed_at"),
        *_point_columns("coordinates", ""),
        _column("hazard_code", _TEXT),
        _column("place_hint", _TEXT),
        _column("media_count", ColumnKind.INTEGER),
        _column("submitted_at", ColumnKind.TIMESTAMP),
    ),
    geometry_fields=("coordinates",),
)
"""Flat layout of the ``reports`` dataset; the point is already rounded."""

_LAYOUTS: Final = {
    layout.dataset: layout for layout in (EVENTS_LAYOUT, CLAIMS_LAYOUT, REPORTS_LAYOUT)
}


def layout_of(dataset: ExportDataset) -> DatasetLayout:
    """Return the flat layout of a dataset.

    Args:
        dataset: The dataset.

    Returns:
        Its layout.
    """
    return _LAYOUTS[dataset]


def require_dataset(row: ExportRow, dataset: ExportDataset) -> None:
    """Check that a row belongs to the dataset being written.

    Args:
        row: The row.
        dataset: The dataset the exporter writes.

    Raises:
        InvariantViolationError: If the row is of another dataset; the handler
            never mixes datasets, so this is a programming error.
    """
    if row.dataset is not dataset:
        message = "an export row does not belong to the dataset being written"
        raise InvariantViolationError(
            message,
            details={"dataset": dataset.value, "row_dataset": row.dataset.value},
        )


def _moment(moment: DateWithPrecision | None) -> tuple[FlatValue, FlatValue]:
    if moment is None:
        return None, None
    return moment.value, moment.precision.value


def _point(point: Coordinates | None) -> tuple[FlatValue, FlatValue]:
    if point is None:
        return None, None
    return point.longitude, point.latitude


def _event_values(row: EventExportRow) -> tuple[FlatValue, ...]:
    return (
        str(row.event_id),
        row.hazard_code,
        row.title,
        row.summary,
        *_moment(row.started_at),
        *_moment(row.ended_at),
        *_point(row.centroid),
        tuple(row.place_codes),
        tuple(str(source_id) for source_id in row.source_ids),
        row.status.value,
        row.verification_state,
        row.updated_at,
    )


def _claim_value_values(row: ClaimExportRow) -> tuple[FlatValue, ...]:
    value = row.value
    if isinstance(value, CountValue):
        return value.value_kind.value, value.count, None, None, None, None, None
    if isinstance(value, MeasurementValue):
        measurement = value.measurement
        return (
            value.value_kind.value,
            None,
            measurement.value,
            measurement.unit,
            None,
            None,
            None,
        )
    return (
        value.value_kind.value,
        None,
        None,
        None,
        value.amount,
        value.currency,
        value.price_year,
    )


def _claim_values(row: ClaimExportRow) -> tuple[FlatValue, ...]:
    scope = row.scope
    return (
        str(row.claim_id),
        str(row.event_id),
        row.metric_code,
        *_claim_value_values(row),
        row.confidence.value,
        str(row.source_id),
        row.source_type,
        *_moment(row.claimed_at),
        scope.place_code,
        None if scope.asset_id is None else str(scope.asset_id),
        row.status.value,
        None if row.supersedes_id is None else str(row.supersedes_id),
        row.created_at,
    )


def _report_values(row: ReportExportRow) -> tuple[FlatValue, ...]:
    return (
        str(row.report_id),
        row.status.value,
        row.revision,
        *_moment(row.observed_at),
        *_point(row.coordinates),
        row.hazard_code,
        row.place_hint,
        row.media_count,
        row.submitted_at,
    )


def flat_values(row: ExportRow) -> tuple[FlatValue, ...]:
    """Return the cells of one row, in the order of its dataset's columns.

    Args:
        row: The row.

    Returns:
        One value per column of ``layout_of(row.dataset)``.
    """
    if isinstance(row, EventExportRow):
        return _event_values(row)
    if isinstance(row, ClaimExportRow):
        return _claim_values(row)
    return _report_values(row)


def geometry_of(row: ExportRow) -> ExportGeometry | None:
    """Return the geometry a spatial format writes for one row.

    Args:
        row: The row.

    Returns:
        An event's geometry, else its centroid as a point, else ``None``; a
        report's rounded point; ``None`` for a claim.
    """
    if isinstance(row, EventExportRow):
        if row.geometry is not None:
            return row.geometry.geojson
        return None if row.centroid is None else row.centroid.to_geojson_point()
    if isinstance(row, ReportExportRow):
        return row.coordinates.to_geojson_point()
    return None


def geometry_mapping(geometry: ExportGeometry) -> dict[str, object]:
    """Return a geometry as a GeoJSON object, without ``bbox`` when absent.

    Args:
        geometry: The geometry.

    Returns:
        The JSON-ready GeoJSON object.
    """
    return geometry.model_dump(mode="json", exclude_none=True)
