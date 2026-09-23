"""GeoJSON export (format code ``geojson``): one RFC 7946 ``FeatureCollection``.

Every row is one ``Feature``, streamed feature by feature:

- ``id`` is the row's id (event, claim or report);
- ``geometry`` is an event's geometry, else its centroid point, else ``null``; a
  report's rounded point; always ``null`` for a claim, which has no location of
  its own (RFC 7946 §3.2 allows unlocated features; **proposed**, see the open
  question on claim geometry);
- ``properties`` is ``row.model_dump(mode="json")`` without the field the geometry
  came from (``geometry`` for events, ``coordinates`` for reports), so a position
  is written once. An event keeps its ``centroid`` property, because a polygon's
  representative point is information the geometry does not state.

Coordinates are WGS84 longitude, latitude as RFC 7946 requires.

Patterns: Strategy.
"""

import json
from collections.abc import AsyncIterator
from typing import Final

from yakhnama.modules.exchange.application.formats import (
    BinarySink,
    EventExportRow,
    ExportRow,
    ReportExportRow,
)
from yakhnama.modules.exchange.domain.registry import GEOJSON_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters.flat_layout import (
    geometry_mapping,
    geometry_of,
    require_dataset,
)

_OPENING: Final = b'{"type":"FeatureCollection","features":[\n'
_CLOSING: Final = b"]}\n"
_FEATURE_SEPARATOR: Final = b",\n"


def _row_id(row: ExportRow) -> str:
    if isinstance(row, EventExportRow):
        return str(row.event_id)
    if isinstance(row, ReportExportRow):
        return str(row.report_id)
    return str(row.claim_id)


def _excluded_properties(row: ExportRow) -> set[str]:
    if isinstance(row, EventExportRow):
        return {"geometry"}
    if isinstance(row, ReportExportRow):
        return {"coordinates"}
    return set()


def feature_of(row: ExportRow) -> dict[str, object]:
    """Return one row as a GeoJSON ``Feature`` object.

    Args:
        row: The row.

    Returns:
        The JSON-ready feature.
    """
    geometry = geometry_of(row)
    return {
        "type": "Feature",
        "id": _row_id(row),
        "geometry": None if geometry is None else geometry_mapping(geometry),
        "properties": row.model_dump(mode="json", exclude=_excluded_properties(row)),
    }


class GeoJsonExporter:
    """Writes a dataset as a GeoJSON ``FeatureCollection``, streamed.

    Implements: Strategy (``Exporter``).
    """

    @property
    def format(self) -> ExportFormat:
        """Return ``geojson``."""
        return ExportFormat.GEOJSON

    @property
    def media_type(self) -> str:
        """Return the descriptor's media type, ``application/geo+json``."""
        return GEOJSON_DESCRIPTOR.media_type

    async def write(
        self,
        dataset: ExportDataset,
        rows: AsyncIterator[ExportRow],
        sink: BinarySink,
    ) -> None:
        """Write the collection's opening, each feature, then its closing.

        Args:
            dataset: The dataset every row belongs to.
            rows: The rows.
            sink: Where the bytes go.

        Raises:
            InvariantViolationError: If a row is of another dataset.
            ValidationError: If the sink refuses more bytes.
        """
        await sink.write(_OPENING)
        is_first = True
        async for row in rows:
            require_dataset(row, dataset)
            encoded = json.dumps(
                feature_of(row),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            await sink.write(encoded if is_first else _FEATURE_SEPARATOR + encoded)
            is_first = False
        await sink.write(_CLOSING if is_first else b"\n" + _CLOSING)
