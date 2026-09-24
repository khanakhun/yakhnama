"""Unit tests of the GeoJSON importer."""

import asyncio
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.modules.exchange.domain.test_backfill import drafts
from tests.unit.modules.exchange.infrastructure.adapters.support import (
    ChunkedSource,
    import_rows,
)
from yakhnama.modules.exchange.domain.backfill import (
    ImportedEventDraft,
    require_backfill_header,
)
from yakhnama.modules.exchange.domain.errors import ImportContractError
from yakhnama.modules.exchange.domain.value_objects import ImportFormat
from yakhnama.modules.exchange.infrastructure.adapters.geojson_importer import (
    ENCODING_REASON,
    JSON_REASON,
    NOT_A_FEATURE_COLLECTION_REASON,
    NOT_A_FEATURE_REASON,
    GeoJsonImporter,
    JsonValue,
    cell_text,
)

POINT = {"type": "Point", "coordinates": [74.5, 36.25]}


def _collection(*features: object) -> bytes:
    return json.dumps(
        {"type": "FeatureCollection", "features": list(features)}
    ).encode()


def _feature(properties: object, geometry: object = None) -> dict[str, object]:
    return {"type": "Feature", "properties": properties, "geometry": geometry}


def test_geojson_importer_declares_geojson_format() -> None:
    importer = GeoJsonImporter()

    code = importer.format

    assert code is ImportFormat.GEOJSON


async def test_geojson_importer_header_lists_properties_in_order_then_geometry() -> (
    None
):
    data = _collection(
        _feature({"title": "A", "hazard_type": "glof"}, POINT),
        _feature({"summary": None, "title": "B"}),
    )

    header, _ = await import_rows(GeoJsonImporter(chunk_bytes=7), data)

    assert header == ("title", "hazard_type", "summary", "geometry")


async def test_geojson_importer_yields_properties_as_text_and_geometry_as_json() -> (
    None
):
    properties = {
        "title": "Flood, گلگت",
        "count": 12,
        "ratio": 0.1,
        "flag": True,
        "other": False,
        "absent": None,
        "nested": {"a": [1, "b"]},
    }
    data = _collection(_feature(properties, POINT), _feature(None))

    _, rows = await import_rows(GeoJsonImporter(), data)

    assert rows[0] == (
        1,
        {
            "title": "Flood, گلگت",
            "count": "12",
            "ratio": "0.1",
            "flag": "true",
            "other": "false",
            "nested": '{"a":[1,"b"]}',
            "geometry": '{"type":"Point","coordinates":[74.5,36.25]}',
        },
    )
    assert rows[1] == (2, {})


async def test_geojson_importer_property_named_geometry_repeats_header_column() -> None:
    data = _collection(_feature({"geometry": "x"}, POINT))

    header, _ = await import_rows(GeoJsonImporter(), data)

    assert header == ("geometry", "geometry")
    with pytest.raises(ImportContractError):
        require_backfill_header(header)


async def test_geojson_importer_tolerates_byte_order_mark() -> None:
    data = b"\xef\xbb\xbf" + _collection(_feature({"title": "A"}))

    _, rows = await import_rows(GeoJsonImporter(), data)

    assert rows == [(1, {"title": "A"})]


async def test_geojson_importer_read_without_header_call_parses_first() -> None:
    importer = GeoJsonImporter()
    source = ChunkedSource(_collection(_feature({"title": "A"})))

    rows = [row async for row in importer.read(source)]

    assert rows == [(1, {"title": "A"})]


@pytest.mark.parametrize(
    ("data", "details"),
    [
        (b"\xff{}", {"reason": ENCODING_REASON}),
        (b"{not json", {"reason": JSON_REASON}),
        (b'{"type": NaN}', {"reason": JSON_REASON}),
        (b'{"type": "FeatureCollection", "type": "x"}', {"reason": JSON_REASON}),
        (b"[" * 100_000, {"reason": JSON_REASON}),
        (b"[]", {"reason": NOT_A_FEATURE_COLLECTION_REASON}),
        (b'{"type": "Feature"}', {"reason": NOT_A_FEATURE_COLLECTION_REASON}),
        (
            b'{"type": "FeatureCollection", "features": {}}',
            {"reason": NOT_A_FEATURE_COLLECTION_REASON},
        ),
        (
            _collection(_feature({}), {"type": "Point"}),
            {"reason": NOT_A_FEATURE_REASON, "feature_number": 2},
        ),
        (
            _collection(_feature(["not", "an", "object"])),
            {"reason": NOT_A_FEATURE_REASON, "feature_number": 1},
        ),
        (
            _collection(_feature({}, "POINT (1 2)")),
            {"reason": NOT_A_FEATURE_REASON, "feature_number": 1},
        ),
    ],
)
async def test_geojson_importer_malformed_file_raises_contract_error(
    data: bytes, details: dict[str, object]
) -> None:
    with pytest.raises(ImportContractError) as caught:
        await import_rows(GeoJsonImporter(), data)

    assert caught.value.details == details


@pytest.mark.parametrize(
    ("value", "expected"),
    [("text", "text"), (None, None), (True, "true"), (3, "3"), (1e20, "1e+20")],
)
def test_cell_text_of_property_values(value: JsonValue, expected: str | None) -> None:
    text = cell_text(value)

    assert text == expected


def _feature_of_flat_row(cells: dict[str, str]) -> dict[str, object]:
    """Write a backfill row as a feature: a point or geometry cell as ``geometry``."""
    properties = {name: text for name, text in cells.items() if text}
    longitude = properties.pop("longitude", None)
    latitude = properties.pop("latitude", None)
    geometry_text = properties.pop("geometry", None)
    geometry: object = None
    if longitude is not None and latitude is not None:
        geometry = {
            "type": "Point",
            "coordinates": [float(longitude), float(latitude)],
        }
    elif geometry_text is not None:
        geometry = json.loads(geometry_text)
    return _feature(properties, geometry)


@settings(max_examples=60, deadline=None)
@given(draft_list=st.lists(drafts(), min_size=1, max_size=4))
def test_geojson_import_of_exported_drafts_returns_equal_drafts(
    draft_list: list[ImportedEventDraft],
) -> None:
    data = _collection(
        *(_feature_of_flat_row(draft.to_flat_row()) for draft in draft_list)
    )

    header, rows = asyncio.run(import_rows(GeoJsonImporter(), data))

    require_backfill_header(header)
    restored = [
        ImportedEventDraft.from_flat_row(number, cells) for number, cells in rows
    ]
    assert restored == draft_list
