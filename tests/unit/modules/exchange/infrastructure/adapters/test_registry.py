"""Unit tests of the default format adapter registry."""

from yakhnama.modules.exchange.domain.registry import DEFAULT_FORMAT_REGISTRY
from yakhnama.modules.exchange.domain.value_objects import ExportFormat, ImportFormat
from yakhnama.modules.exchange.infrastructure.adapters.csv_exporter import CsvExporter
from yakhnama.modules.exchange.infrastructure.adapters.csv_importer import CsvImporter
from yakhnama.modules.exchange.infrastructure.adapters.geojson_exporter import (
    GeoJsonExporter,
)
from yakhnama.modules.exchange.infrastructure.adapters.geojson_importer import (
    GeoJsonImporter,
)
from yakhnama.modules.exchange.infrastructure.adapters.geoparquet_exporter import (
    GeoParquetExporter,
)
from yakhnama.modules.exchange.infrastructure.adapters.json_exporter import (
    JsonExporter,
)
from yakhnama.modules.exchange.infrastructure.adapters.registry import (
    default_format_adapters,
)


def test_default_format_adapters_registers_every_export_format() -> None:
    registry = default_format_adapters()

    exporters = {code: type(registry.exporter(code)) for code in ExportFormat}

    assert exporters == {
        ExportFormat.JSON: JsonExporter,
        ExportFormat.GEOJSON: GeoJsonExporter,
        ExportFormat.CSV: CsvExporter,
        ExportFormat.GEOPARQUET: GeoParquetExporter,
    }


def test_default_format_adapters_registers_every_import_format() -> None:
    registry = default_format_adapters()

    importers = {code: type(registry.importer(code)) for code in ImportFormat}

    assert importers == {
        ImportFormat.CSV: CsvImporter,
        ImportFormat.GEOJSON: GeoJsonImporter,
    }


def test_default_format_adapters_labels_files_from_domain_descriptors() -> None:
    registry = default_format_adapters()

    descriptors = [registry.export_descriptor(code) for code in ExportFormat]

    assert descriptors == [
        DEFAULT_FORMAT_REGISTRY.export_descriptor(code.value) for code in ExportFormat
    ]
