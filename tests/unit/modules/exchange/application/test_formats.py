"""Unit tests for the format adapter registry and the export rows."""

import pydantic
import pytest

from tests.fakes.exchange import FakeExporter, FakeImporter
from tests.unit.modules.exchange.application.support import (
    claim_row,
    event_row,
    report_row,
)
from yakhnama.modules.exchange.application.formats import FormatAdapterRegistry
from yakhnama.modules.exchange.domain.errors import UnsupportedFormatError
from yakhnama.modules.exchange.domain.registry import FormatRegistry
from yakhnama.modules.exchange.domain.value_objects import (
    ExportDataset,
    ExportFormat,
    ImportFormat,
)
from yakhnama.shared_kernel.errors import ConflictError


def test_registry_returns_registered_strategies_and_descriptors() -> None:
    exporter = FakeExporter()
    importer = FakeImporter()

    registry = (
        FormatAdapterRegistry().register_exporter(exporter).register_importer(importer)
    )

    assert registry.exporter(ExportFormat.CSV) is exporter
    assert registry.importer(ImportFormat.CSV) is importer
    assert registry.export_descriptor(ExportFormat.CSV).extension == ".csv"
    assert registry.import_descriptor(ImportFormat.CSV).media_type == "text/csv"


def test_registry_register_same_exporter_twice_raises_conflict() -> None:
    registry = FormatAdapterRegistry().register_exporter(FakeExporter())

    with pytest.raises(ConflictError):
        registry.register_exporter(FakeExporter())


def test_registry_register_same_importer_twice_raises_conflict() -> None:
    registry = FormatAdapterRegistry().register_importer(FakeImporter())

    with pytest.raises(ConflictError):
        registry.register_importer(FakeImporter())


def test_registry_exporter_with_wrong_media_type_raises_conflict() -> None:
    registry = FormatAdapterRegistry()

    with pytest.raises(ConflictError):
        registry.register_exporter(FakeExporter(media_type="application/json"))


def test_registry_format_unknown_to_domain_catalogue_raises_unsupported() -> None:
    registry = FormatAdapterRegistry(FormatRegistry())

    with pytest.raises(UnsupportedFormatError):
        registry.register_exporter(FakeExporter())
    with pytest.raises(UnsupportedFormatError):
        registry.register_importer(FakeImporter())


def test_registry_lookup_of_unregistered_format_raises_unsupported() -> None:
    registry = FormatAdapterRegistry()

    with pytest.raises(UnsupportedFormatError):
        registry.exporter(ExportFormat.GEOPARQUET)
    with pytest.raises(UnsupportedFormatError):
        registry.importer(ImportFormat.GEOJSON)
    with pytest.raises(UnsupportedFormatError):
        registry.export_descriptor(ExportFormat.JSON)
    with pytest.raises(UnsupportedFormatError):
        registry.import_descriptor(ImportFormat.CSV)


def test_export_rows_declare_their_dataset() -> None:
    datasets = (event_row().dataset, claim_row().dataset, report_row().dataset)

    assert datasets == (
        ExportDataset.EVENTS,
        ExportDataset.CLAIMS,
        ExportDataset.REPORTS,
    )


def test_export_rows_are_frozen_and_refuse_unknown_fields() -> None:
    row = event_row()

    with pytest.raises(pydantic.ValidationError):
        row.model_validate({**dict(row), "reporter_id": "x"})
    with pytest.raises(pydantic.ValidationError):
        row.title = "Changed"  # type: ignore[misc]  # reason: proving the model is frozen
