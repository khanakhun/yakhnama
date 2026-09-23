"""Unit tests of the flat column layout shared by CSV and GeoParquet."""

from decimal import Decimal

import pytest
from pydantic import BaseModel

from tests.unit.modules.exchange.application.support import (
    claim_row,
    day,
    event_row,
    report_row,
)
from yakhnama.modules.events.public import EventGeometry
from yakhnama.modules.exchange.application.formats import (
    ClaimExportRow,
    EventExportRow,
    ReportExportRow,
)
from yakhnama.modules.exchange.domain.value_objects import ExportDataset
from yakhnama.modules.exchange.infrastructure.adapters.flat_layout import (
    CLAIMS_LAYOUT,
    EVENTS_LAYOUT,
    REPORTS_LAYOUT,
    DatasetLayout,
    flat_values,
    geometry_mapping,
    geometry_of,
    layout_of,
    require_dataset,
)
from yakhnama.modules.impacts.public import (
    ClaimScope,
    MeasurementValue,
    MonetaryValue,
)
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.value_objects import Measurement

POLYGON = EventGeometry.model_validate(
    {
        "geojson": {
            "type": "Polygon",
            "coordinates": [[[74.0, 36.0], [74.5, 36.0], [74.0, 36.5], [74.0, 36.0]]],
        }
    }
)
LAYOUTS: list[tuple[DatasetLayout, type[BaseModel]]] = [
    (EVENTS_LAYOUT, EventExportRow),
    (CLAIMS_LAYOUT, ClaimExportRow),
    (REPORTS_LAYOUT, ReportExportRow),
]


@pytest.mark.parametrize(("layout", "model"), LAYOUTS)
def test_layout_covers_every_field_of_its_row_model(
    layout: DatasetLayout, model: type[BaseModel]
) -> None:
    covered = {column.field for column in layout.columns} | set(layout.geometry_fields)

    fields = set(model.model_fields)

    assert covered == fields


@pytest.mark.parametrize(("layout", "model"), LAYOUTS)
def test_layout_column_names_are_distinct(
    layout: DatasetLayout, model: type[BaseModel]
) -> None:
    names = layout.names

    assert len(set(names)) == len(names)
    assert layout_of(model.dataset) is layout  # type: ignore[attr-defined]  # reason: ClassVar of the export row DTOs, absent on BaseModel


def test_layout_has_geometry_only_for_spatial_datasets() -> None:
    spatial = [layout.has_geometry for layout, _ in LAYOUTS]

    assert spatial == [True, False, True]


def test_flat_values_of_event_follow_event_columns() -> None:
    row = event_row(
        summary="A summary",
        ended_at=day(2022, 7, 20),
        geometry=POLYGON,
    )

    values = dict(zip(EVENTS_LAYOUT.names, flat_values(row), strict=True))

    assert values["event_id"] == str(row.event_id)
    assert values["started_at"] == row.started_at.value
    assert values["started_at_precision"] == "day"
    assert values["ended_at_precision"] == "day"
    assert values["centroid_longitude"] == 74.5
    assert values["centroid_latitude"] == 36.25
    assert values["place_codes"] == ("pk.gb.test",)
    assert values["source_ids"] == tuple(str(item) for item in row.source_ids)
    assert values["status"] == "published"
    assert values["updated_at"] == row.updated_at


def test_flat_values_of_event_without_end_or_centroid_are_absent() -> None:
    row = event_row(centroid=None)

    values = dict(zip(EVENTS_LAYOUT.names, flat_values(row), strict=True))

    assert values["ended_at"] is None
    assert values["ended_at_precision"] is None
    assert values["centroid_longitude"] is None
    assert values["summary"] is None


def test_flat_values_of_claims_fill_only_their_variant_columns() -> None:
    asset_id = event_row().event_id
    count = claim_row(scope=ClaimScope(place_code="pk.gb.test", asset_id=asset_id))
    measured = claim_row(
        value=MeasurementValue(measurement=Measurement(value=2.5, unit="metre")),
        supersedes_id=asset_id,
    )
    monetary = claim_row(
        value=MonetaryValue(amount=Decimal("1500.50"), currency="PKR", price_year=2022)
    )

    rows = [
        dict(zip(CLAIMS_LAYOUT.names, flat_values(row), strict=True))
        for row in (count, measured, monetary)
    ]

    assert [row["value_kind"] for row in rows] == ["count", "measurement", "monetary"]
    assert (rows[0]["count"], rows[0]["measurement_value"], rows[0]["amount"]) == (
        12,
        None,
        None,
    )
    assert rows[0]["scope_place_code"] == "pk.gb.test"
    assert rows[0]["scope_asset_id"] == str(asset_id)
    assert (rows[1]["measurement_value"], rows[1]["measurement_unit"]) == (
        2.5,
        "metre",
    )
    assert rows[1]["supersedes_id"] == str(asset_id)
    assert (rows[2]["amount"], rows[2]["currency"], rows[2]["price_year"]) == (
        Decimal("1500.50"),
        "PKR",
        2022,
    )
    assert rows[2]["count"] is None


def test_flat_values_of_report_follow_report_columns() -> None:
    row = report_row(longitude=-74.25, latitude=36.5)

    values = dict(zip(REPORTS_LAYOUT.names, flat_values(row), strict=True))

    assert (values["longitude"], values["latitude"]) == (-74.25, 36.5)
    assert values["revision"] == 1
    assert values["media_count"] == 1
    assert values["observed_at_precision"] == "day"


def test_geometry_of_event_prefers_geometry_then_centroid() -> None:
    mapped = event_row(geometry=POLYGON)
    pinned = event_row()
    unlocated = event_row(centroid=None)

    geometries = [geometry_of(row) for row in (mapped, pinned, unlocated)]

    assert geometries[0] == POLYGON.geojson
    assert geometries[1] is not None
    assert geometry_mapping(geometries[1]) == {
        "type": "Point",
        "coordinates": [74.5, 36.25],
    }
    assert geometries[2] is None


def test_geometry_of_report_is_its_point_and_of_claim_is_none() -> None:
    report = geometry_of(report_row(longitude=74.12, latitude=36.98))

    claim = geometry_of(claim_row())

    assert report is not None
    assert geometry_mapping(report)["coordinates"] == [74.12, 36.98]
    assert claim is None


def test_require_dataset_with_other_dataset_raises_invariant_violation() -> None:
    row = claim_row()

    with pytest.raises(InvariantViolationError) as caught:
        require_dataset(row, ExportDataset.EVENTS)

    assert caught.value.details == {"dataset": "events", "row_dataset": "claims"}


def test_require_dataset_with_same_dataset_passes() -> None:
    row = report_row()

    require_dataset(row, ExportDataset.REPORTS)

    assert row.dataset is ExportDataset.REPORTS
