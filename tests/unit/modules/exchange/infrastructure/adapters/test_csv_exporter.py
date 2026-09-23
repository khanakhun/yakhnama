"""Unit tests of the CSV exporter."""

import csv
import io
import json
from decimal import Decimal

import pytest

from tests.unit.modules.exchange.application.support import (
    claim_row,
    event_row,
    report_row,
)
from tests.unit.modules.exchange.infrastructure.adapters.support import export_bytes
from yakhnama.modules.events.public import EventGeometry
from yakhnama.modules.exchange.domain.registry import CSV_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters.csv_exporter import (
    CsvExporter,
    cell_text,
    defuse_formula,
    header_of,
)
from yakhnama.modules.exchange.infrastructure.adapters.flat_layout import (
    CLAIMS_LAYOUT,
    EVENTS_LAYOUT,
    REPORTS_LAYOUT,
    DatasetLayout,
)
from yakhnama.modules.impacts.public import MonetaryValue
from yakhnama.shared_kernel.errors import InvariantViolationError

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[74.0, 36.0], [74.5, 36.0], [74.0, 36.5], [74.0, 36.0]]],
}


def _records(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8"), newline="")))


def test_csv_exporter_declares_format_and_descriptor_media_type() -> None:
    exporter = CsvExporter()

    labels = (exporter.format, exporter.media_type)

    assert labels == (ExportFormat.CSV, CSV_DESCRIPTOR.media_type)


def test_header_of_spatial_dataset_ends_with_geometry() -> None:
    headers = [header_of(layout) for layout in (EVENTS_LAYOUT, CLAIMS_LAYOUT)]

    assert headers[0] == (*EVENTS_LAYOUT.names, "geometry")
    assert headers[1] == CLAIMS_LAYOUT.names


@pytest.mark.parametrize(
    ("dataset", "layout"),
    [
        (ExportDataset.EVENTS, EVENTS_LAYOUT),
        (ExportDataset.CLAIMS, CLAIMS_LAYOUT),
        (ExportDataset.REPORTS, REPORTS_LAYOUT),
    ],
)
async def test_csv_exporter_without_rows_writes_only_header(
    dataset: ExportDataset, layout: DatasetLayout
) -> None:
    sink = await export_bytes(CsvExporter(), dataset, [])

    lines = sink.content.decode("utf-8").split("\r\n")
    assert lines[1:] == [""]
    assert tuple(lines[0].split(",")) == header_of(layout)


async def test_csv_exporter_quotes_commas_quotes_newlines_and_keeps_urdu() -> None:
    title = 'Flood at "Upper" valley, گلگت'
    summary = "First line\nsecond line, with a comma"
    row = event_row(title=title, summary=summary)

    sink = await export_bytes(CsvExporter(), ExportDataset.EVENTS, [row])

    assert not sink.content.startswith(b"\xef\xbb\xbf")
    record = _records(sink.content)[0]
    assert record["title"] == title
    assert record["summary"] == summary
    assert record["event_id"] == str(row.event_id)
    assert record["started_at"] == "2022-07-15T00:00:00+00:00"
    assert record["centroid_longitude"] == "74.5"
    assert record["place_codes"] == "pk.gb.test"
    assert record["ended_at"] == ""
    assert json.loads(record["geometry"]) == {
        "type": "Point",
        "coordinates": [74.5, 36.25],
    }


async def test_csv_exporter_writes_polygon_geometry_as_geojson_text() -> None:
    row = event_row(geometry=EventGeometry.model_validate({"geojson": POLYGON}))

    sink = await export_bytes(CsvExporter(), ExportDataset.EVENTS, [row])

    assert json.loads(_records(sink.content)[0]["geometry"]) == POLYGON


async def test_csv_exporter_event_without_location_has_empty_geometry() -> None:
    row = event_row(centroid=None)

    sink = await export_bytes(CsvExporter(), ExportDataset.EVENTS, [row])

    assert _records(sink.content)[0]["geometry"] == ""


@pytest.mark.parametrize("formula", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx"])
async def test_csv_exporter_defuses_formula_in_text_cells(formula: str) -> None:
    row = event_row(title=f"{formula} synthetic")

    sink = await export_bytes(CsvExporter(), ExportDataset.EVENTS, [row])

    assert _records(sink.content)[0]["title"] == f"'{formula} synthetic"


async def test_csv_exporter_never_prefixes_negative_numbers() -> None:
    row = report_row(longitude=-74.25, latitude=-36.5)

    sink = await export_bytes(CsvExporter(), ExportDataset.REPORTS, [row])

    record = _records(sink.content)[0]
    assert (record["longitude"], record["latitude"]) == ("-74.25", "-36.5")
    assert json.loads(record["geometry"])["coordinates"] == [-74.25, -36.5]


async def test_csv_exporter_writes_claim_value_columns() -> None:
    row = claim_row(
        value=MonetaryValue(amount=Decimal("1500.50"), currency="PKR", price_year=2022)
    )

    sink = await export_bytes(CsvExporter(), ExportDataset.CLAIMS, [row])

    record = _records(sink.content)[0]
    assert "geometry" not in record
    assert (record["value_kind"], record["amount"], record["currency"]) == (
        "monetary",
        "1500.50",
        "PKR",
    )
    assert (record["count"], record["price_year"]) == ("", "2022")


async def test_csv_exporter_ends_records_with_crlf_and_writes_per_row() -> None:
    rows = [claim_row(), claim_row()]

    sink = await export_bytes(CsvExporter(), ExportDataset.CLAIMS, rows)

    assert len(sink.writes) == 3
    assert all(write.endswith(b"\r\n") for write in sink.writes)


async def test_csv_exporter_with_row_of_other_dataset_raises() -> None:
    with pytest.raises(InvariantViolationError):
        await export_bytes(CsvExporter(), ExportDataset.CLAIMS, [report_row()])


@pytest.mark.parametrize(
    ("text", "expected"),
    [("=A1", "'=A1"), ("plain", "plain"), ("", ""), ("a=b", "a=b"), ("\rx", "'\rx")],
)
def test_defuse_formula_prefixes_only_formula_starts(text: str, expected: str) -> None:
    defused = defuse_formula(text)

    assert defused == expected


def test_cell_text_writes_lossless_floats_and_joined_lists() -> None:
    texts = [cell_text(0.1 + 0.2), cell_text(("a", "b")), cell_text(None), cell_text(3)]

    assert texts == [repr(0.1 + 0.2), "a;b", "", "3"]
