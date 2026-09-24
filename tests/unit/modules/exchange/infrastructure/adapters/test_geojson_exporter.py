"""Unit tests of the GeoJSON exporter."""

import json

import pytest

from tests.unit.modules.exchange.application.support import (
    claim_row,
    event_row,
    report_row,
)
from tests.unit.modules.exchange.infrastructure.adapters.support import export_bytes
from yakhnama.modules.events.public import EventGeometry
from yakhnama.modules.exchange.domain.registry import GEOJSON_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters.geojson_exporter import (
    GeoJsonExporter,
    feature_of,
)
from yakhnama.shared_kernel.errors import InvariantViolationError

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[74.0, 36.0], [74.5, 36.0], [74.0, 36.5], [74.0, 36.0]]],
}


def test_geojson_exporter_declares_format_and_descriptor_media_type() -> None:
    exporter = GeoJsonExporter()

    labels = (exporter.format, exporter.media_type)

    assert labels == (ExportFormat.GEOJSON, GEOJSON_DESCRIPTOR.media_type)


async def test_geojson_exporter_writes_feature_collection_of_events() -> None:
    mapped = event_row(geometry=EventGeometry.model_validate({"geojson": POLYGON}))
    pinned = event_row(title="Synthetic event, بلتستان")
    unlocated = event_row(centroid=None)

    sink = await export_bytes(
        GeoJsonExporter(), ExportDataset.EVENTS, [mapped, pinned, unlocated]
    )

    document = json.loads(sink.content)
    assert document["type"] == "FeatureCollection"
    features = document["features"]
    assert [feature["id"] for feature in features] == [
        str(row.event_id) for row in (mapped, pinned, unlocated)
    ]
    assert features[0]["geometry"] == POLYGON
    assert features[1]["geometry"] == {"type": "Point", "coordinates": [74.5, 36.25]}
    assert features[2]["geometry"] is None
    assert "geometry" not in features[0]["properties"]
    assert features[0]["properties"]["centroid"] == {
        "longitude": 74.5,
        "latitude": 36.25,
    }
    assert features[1]["properties"]["title"] == "Synthetic event, بلتستان"
    assert len(sink.writes) == 5


async def test_geojson_exporter_writes_report_point_without_coordinates_property() -> (
    None
):
    row = report_row(longitude=74.12, latitude=36.98)

    sink = await export_bytes(GeoJsonExporter(), ExportDataset.REPORTS, [row])

    feature = json.loads(sink.content)["features"][0]
    assert feature["id"] == str(row.report_id)
    assert feature["geometry"] == {"type": "Point", "coordinates": [74.12, 36.98]}
    assert "coordinates" not in feature["properties"]
    assert feature["properties"]["report_id"] == str(row.report_id)


async def test_geojson_exporter_writes_claims_with_null_geometry() -> None:
    row = claim_row()

    sink = await export_bytes(GeoJsonExporter(), ExportDataset.CLAIMS, [row])

    feature = json.loads(sink.content)["features"][0]
    assert feature["id"] == str(row.claim_id)
    assert feature["geometry"] is None
    assert feature["properties"] == row.model_dump(mode="json")


async def test_geojson_exporter_without_rows_writes_empty_collection() -> None:
    sink = await export_bytes(GeoJsonExporter(), ExportDataset.CLAIMS, [])

    assert json.loads(sink.content) == {"type": "FeatureCollection", "features": []}


async def test_geojson_exporter_with_row_of_other_dataset_raises() -> None:
    with pytest.raises(InvariantViolationError):
        await export_bytes(GeoJsonExporter(), ExportDataset.REPORTS, [event_row()])


def test_feature_of_event_is_a_geojson_feature() -> None:
    row = event_row()

    feature = feature_of(row)

    assert feature["type"] == "Feature"
    assert feature["id"] == str(row.event_id)
