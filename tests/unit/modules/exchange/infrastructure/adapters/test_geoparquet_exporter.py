"""Unit tests of the GeoParquet exporter, read back with pyarrow and Shapely."""

import io
import json
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
import shapely.geometry

from tests.unit.modules.exchange.application.support import (
    claim_row,
    event_row,
    report_row,
)
from tests.unit.modules.exchange.infrastructure.adapters.support import (
    RecordingSink,
    export_bytes,
    rows_of,
)
from yakhnama.modules.events.public import EventGeometry
from yakhnama.modules.exchange.domain.registry import GEOPARQUET_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters import geoparquet_exporter
from yakhnama.modules.exchange.infrastructure.adapters.flat_layout import (
    CLAIMS_LAYOUT,
    EVENTS_LAYOUT,
    flat_values,
)
from yakhnama.modules.exchange.infrastructure.adapters.geoparquet_exporter import (
    GeoParquetExporter,
    arrow_schema,
)
from yakhnama.modules.impacts.public import MonetaryValue
from yakhnama.shared_kernel.errors import InvariantViolationError

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[74.0, 36.0], [74.5, 36.0], [74.0, 36.5], [74.0, 36.0]]],
}


def _table(content: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(content))


def _geo(content: bytes) -> dict[str, object]:
    metadata = pq.ParquetFile(io.BytesIO(content)).metadata.metadata
    assert metadata is not None
    loaded: dict[str, object] = json.loads(metadata[b"geo"])
    return loaded


def test_geoparquet_exporter_declares_format_and_descriptor_media_type() -> None:
    exporter = GeoParquetExporter()

    labels = (exporter.format, exporter.media_type, exporter.batch_rows)

    assert labels == (ExportFormat.GEOPARQUET, GEOPARQUET_DESCRIPTOR.media_type, 1000)


async def test_geoparquet_exporter_round_trips_every_event_field() -> None:
    mapped = event_row(
        geometry=EventGeometry.model_validate({"geojson": POLYGON}),
        title="Synthetic flood, گلگت",
    )
    pinned = event_row()
    unlocated = event_row(centroid=None)
    rows = [mapped, pinned, unlocated]

    sink = await export_bytes(GeoParquetExporter(), ExportDataset.EVENTS, rows)

    table = _table(sink.content)
    assert table.schema.names == [*EVENTS_LAYOUT.names, "geometry"]
    records = table.to_pylist()
    for row, record in zip(rows, records, strict=True):
        # Arrow reads a list column back as a Python list, not a tuple.
        expected: dict[str, object] = {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in zip(EVENTS_LAYOUT.names, flat_values(row), strict=True)
        }
        assert {name: record[name] for name in EVENTS_LAYOUT.names} == expected
    geometries = [
        None if record["geometry"] is None else shapely.from_wkb(record["geometry"])
        for record in records
    ]
    assert geometries[0] == shapely.geometry.shape(POLYGON)
    assert geometries[1] == shapely.geometry.Point(74.5, 36.25)
    assert geometries[2] is None


async def test_geoparquet_exporter_writes_geoparquet_1_1_metadata() -> None:
    rows = [
        event_row(geometry=EventGeometry.model_validate({"geojson": POLYGON})),
        event_row(),
        event_row(centroid=None),
    ]

    sink = await export_bytes(GeoParquetExporter(), ExportDataset.EVENTS, rows)

    geo = _geo(sink.content)
    assert geo == {
        "version": "1.1.0",
        "primary_column": "geometry",
        "columns": {
            "geometry": {
                "encoding": "WKB",
                "geometry_types": ["Point", "Polygon"],
                "bbox": [74.0, 36.0, 74.5, 36.5],
            }
        },
    }
    assert _table(sink.content).schema.metadata[b"geo"] == json.dumps(
        geo, separators=(",", ":")
    ).encode("utf-8")


async def test_geoparquet_exporter_keeps_arrow_types() -> None:
    sink = await export_bytes(GeoParquetExporter(), ExportDataset.EVENTS, [])

    schema = _table(sink.content).schema

    assert schema.remove_metadata() == arrow_schema(EVENTS_LAYOUT)
    assert schema.field("updated_at").type == pa.timestamp("us", tz="UTC")


async def test_geoparquet_exporter_writes_one_row_group_per_batch_streamed() -> None:
    rows = [report_row(longitude=74.0 + index, latitude=36.0) for index in range(5)]

    sink = await export_bytes(
        GeoParquetExporter(batch_rows=2), ExportDataset.REPORTS, rows
    )

    parquet = pq.ParquetFile(io.BytesIO(sink.content))
    assert parquet.metadata.num_row_groups == 3
    assert parquet.metadata.num_rows == 5
    assert len(sink.writes) == 3
    assert _geo(sink.content)["columns"] == {
        "geometry": {
            "encoding": "WKB",
            "geometry_types": ["Point"],
            "bbox": [74.0, 36.0, 78.0, 36.0],
        }
    }


async def test_geoparquet_exporter_writes_claims_with_null_geometry() -> None:
    row = claim_row(
        value=MonetaryValue(amount=Decimal("10.25"), currency="USD", price_year=2020)
    )

    sink = await export_bytes(GeoParquetExporter(), ExportDataset.CLAIMS, [row])

    record = _table(sink.content).to_pylist()[0]
    assert record["geometry"] is None
    assert record["amount"] == "10.25"
    assert record["claimed_at"] == row.claimed_at.value
    assert list(record) == [*CLAIMS_LAYOUT.names, "geometry"]
    assert _geo(sink.content)["columns"] == {
        "geometry": {"encoding": "WKB", "geometry_types": []}
    }


async def test_geoparquet_exporter_without_rows_writes_valid_empty_file() -> None:
    sink = await export_bytes(GeoParquetExporter(), ExportDataset.EVENTS, [])

    assert sink.content.startswith(b"PAR1")
    assert _table(sink.content).num_rows == 0


async def test_geoparquet_exporter_with_row_of_other_dataset_forwards_nothing() -> None:
    sink = RecordingSink()

    with pytest.raises(InvariantViolationError):
        await GeoParquetExporter().write(
            ExportDataset.EVENTS, rows_of([report_row()]), sink
        )

    assert sink.writes == []


def test_byte_queue_counts_position_across_takes() -> None:
    # Private on purpose: pyarrow is its only caller, so it is exercised directly.
    queue = geoparquet_exporter._ByteQueue()
    stream = pa.PythonFile(queue, mode="w")

    stream.write(b"abc")
    first = queue.take()
    stream.write(b"de")

    assert (first, queue.take(), queue.tell(), queue.writable()) == (
        b"abc",
        b"de",
        5,
        True,
    )
