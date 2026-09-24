"""Unit tests for ``yakhnama.modules.exchange.domain.registry``."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.exchange.domain.errors import UnsupportedFormatError
from yakhnama.modules.exchange.domain.registry import (
    CSV_DESCRIPTOR,
    DEFAULT_FORMAT_REGISTRY,
    GEOJSON_DESCRIPTOR,
    FormatDescriptor,
    FormatRegistry,
)
from yakhnama.modules.exchange.domain.value_objects import ExportFormat, ImportFormat
from yakhnama.shared_kernel.errors import ConflictError

GEOPACKAGE = FormatDescriptor(
    code="geopackage",
    media_type="application/geopackage+sqlite3",
    extension=".gpkg",
    supports_geometry=True,
)


def test_default_registry_knows_every_export_format() -> None:
    codes = {descriptor.code for descriptor in DEFAULT_FORMAT_REGISTRY.export_formats}

    assert codes == {code.value for code in ExportFormat}


def test_default_registry_knows_every_import_format() -> None:
    codes = {descriptor.code for descriptor in DEFAULT_FORMAT_REGISTRY.import_formats}

    assert codes == {code.value for code in ImportFormat}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("json", ("application/json", ".json", False)),
        ("geojson", ("application/geo+json", ".geojson", True)),
        ("csv", ("text/csv", ".csv", False)),
        ("geoparquet", ("application/vnd.apache.parquet", ".parquet", True)),
    ],
)
def test_default_registry_export_descriptor_returns_labels(
    code: str, expected: tuple[str, str, bool]
) -> None:
    descriptor = DEFAULT_FORMAT_REGISTRY.export_descriptor(code)

    assert (
        descriptor.media_type,
        descriptor.extension,
        descriptor.supports_geometry,
    ) == expected


def test_default_registry_import_descriptor_returns_csv() -> None:
    assert DEFAULT_FORMAT_REGISTRY.import_descriptor("csv") == CSV_DESCRIPTOR


def test_registry_export_descriptor_unknown_code_raises_unsupported_format() -> None:
    with pytest.raises(UnsupportedFormatError) as caught:
        DEFAULT_FORMAT_REGISTRY.export_descriptor("xlsx")

    assert caught.value.details == {"format": "xlsx", "direction": "export"}


def test_registry_import_descriptor_unknown_code_raises_unsupported_format() -> None:
    with pytest.raises(UnsupportedFormatError, match="not supported"):
        DEFAULT_FORMAT_REGISTRY.import_descriptor("geoparquet")


def test_registry_is_supported_tells_directions_apart() -> None:
    assert DEFAULT_FORMAT_REGISTRY.is_export_supported("geoparquet")
    assert not DEFAULT_FORMAT_REGISTRY.is_import_supported("geoparquet")
    assert DEFAULT_FORMAT_REGISTRY.is_import_supported("geojson")
    assert not DEFAULT_FORMAT_REGISTRY.is_export_supported("xlsx")


def test_registry_with_export_format_returns_new_registry_and_keeps_old() -> None:
    extended = DEFAULT_FORMAT_REGISTRY.with_export_format(GEOPACKAGE)

    assert extended.export_descriptor("geopackage") == GEOPACKAGE
    assert not DEFAULT_FORMAT_REGISTRY.is_export_supported("geopackage")
    assert extended.import_formats == DEFAULT_FORMAT_REGISTRY.import_formats


def test_registry_with_import_format_returns_new_registry() -> None:
    extended = FormatRegistry().with_import_format(GEOPACKAGE)

    assert extended.import_descriptor("geopackage") == GEOPACKAGE
    assert extended.export_formats == ()


def test_registry_register_same_export_code_twice_raises_conflict() -> None:
    with pytest.raises(ConflictError) as caught:
        DEFAULT_FORMAT_REGISTRY.with_export_format(GEOJSON_DESCRIPTOR)

    assert caught.value.details == {"format": "geojson", "direction": "export"}


def test_registry_register_same_import_code_twice_raises_conflict() -> None:
    with pytest.raises(ConflictError, match="import"):
        DEFAULT_FORMAT_REGISTRY.with_import_format(CSV_DESCRIPTOR)


def test_registry_built_with_duplicate_codes_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError, match="at most once"):
        FormatRegistry(export_formats=(CSV_DESCRIPTOR, CSV_DESCRIPTOR))


@pytest.mark.parametrize(
    "fields",
    [{"code": "X"}, {"extension": "csv"}, {"media_type": "csv"}],
)
def test_format_descriptor_with_invalid_field_raises_validation_error(
    fields: dict[str, object],
) -> None:
    with pytest.raises(PydanticValidationError):
        FormatDescriptor.model_validate(CSV_DESCRIPTOR.model_dump() | fields)
