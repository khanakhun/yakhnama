"""The default format strategies, registered by format code.

``default_format_adapters`` is what the composition root binds as the exchange
``FormatAdapterRegistry``. A new format is one new adapter file and one line here;
no other format changes.

Patterns: Registry.
"""

from yakhnama.modules.exchange.application.formats import FormatAdapterRegistry
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


def default_format_adapters() -> FormatAdapterRegistry:
    """Return a registry with every exporter and importer of this version.

    Returns:
        JSON, GeoJSON, CSV and GeoParquet exporters; CSV and GeoJSON importers.

    Raises:
        ConflictError: If a strategy's media type disagrees with its domain
            descriptor (a programming error caught at start-up).
    """
    return (
        FormatAdapterRegistry()
        .register_exporter(JsonExporter())
        .register_exporter(GeoJsonExporter())
        .register_exporter(CsvExporter())
        .register_exporter(GeoParquetExporter())
        .register_importer(CsvImporter())
        .register_importer(GeoJsonImporter())
    )
