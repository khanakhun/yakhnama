"""Unit tests of the JSON exporter."""

import json

import pytest

from tests.unit.modules.exchange.application.support import (
    claim_row,
    event_row,
    report_row,
)
from tests.unit.modules.exchange.infrastructure.adapters.support import export_bytes
from yakhnama.modules.exchange.application.formats import ExportRow
from yakhnama.modules.exchange.domain.registry import JSON_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters.json_exporter import (
    JsonExporter,
    encode_row,
)
from yakhnama.shared_kernel.errors import InvariantViolationError


def test_json_exporter_declares_format_and_descriptor_media_type() -> None:
    exporter = JsonExporter()

    labels = (exporter.format, exporter.media_type)

    assert labels == (ExportFormat.JSON, JSON_DESCRIPTOR.media_type)


async def test_json_exporter_writes_array_of_row_dumps_in_order() -> None:
    rows = [event_row(title="Synthetic flood, یخ نامہ"), event_row()]

    sink = await export_bytes(JsonExporter(), ExportDataset.EVENTS, rows)

    assert json.loads(sink.content) == [row.model_dump(mode="json") for row in rows]
    assert "یخ نامہ".encode() in sink.content
    assert len(sink.writes) == len(rows) + 2


@pytest.mark.parametrize(
    ("dataset", "row"),
    [
        (ExportDataset.CLAIMS, claim_row()),
        (ExportDataset.REPORTS, report_row()),
    ],
)
async def test_json_exporter_writes_every_dataset(
    dataset: ExportDataset, row: ExportRow
) -> None:
    sink = await export_bytes(JsonExporter(), dataset, [row])

    assert json.loads(sink.content) == [row.model_dump(mode="json")]


async def test_json_exporter_without_rows_writes_empty_array() -> None:
    sink = await export_bytes(JsonExporter(), ExportDataset.EVENTS, [])

    assert json.loads(sink.content) == []


async def test_json_exporter_with_row_of_other_dataset_raises() -> None:
    with pytest.raises(InvariantViolationError):
        await export_bytes(JsonExporter(), ExportDataset.EVENTS, [claim_row()])


def test_encode_row_is_compact_utf8_json() -> None:
    row = event_row(title="Glacier, خ")

    encoded = encode_row(row)

    assert b"\n" not in encoded
    assert b", " not in encoded.replace("Glacier, خ".encode(), b"")
    assert json.loads(encoded)["title"] == "Glacier, خ"
