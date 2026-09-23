"""The catalogue of exchange formats: which exist, their media types and extensions.

The domain records *that* a format exists and how its files are labelled. The code
that reads or writes a format (an ``Exporter`` or ``Importer`` strategy) lives in the
application's Protocols and in infrastructure, and is looked up by the same code.

Media types: ``application/json`` (RFC 8259), ``application/geo+json`` (RFC 7946),
``text/csv`` (RFC 4180) and ``application/vnd.apache.parquet`` for GeoParquet
(**proposed**: GeoParquet has no media type of its own).

Patterns: Registry, Value Object.
"""

from collections.abc import Iterable
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yakhnama.modules.exchange.domain.errors import UnsupportedFormatError
from yakhnama.modules.exchange.domain.value_objects import (
    ExportFormat,
    FormatCode,
    ImportFormat,
    MediaTypeName,
)
from yakhnama.shared_kernel.errors import ConflictError

EXTENSION_PATTERN: Final = r"^\.[a-z0-9]{1,15}$"
REGISTRY_MAX_FORMATS: Final = 64


class FormatDescriptor(BaseModel):
    """How files of one format are labelled.

    Implements: Value Object.

    Attributes:
        code: Registry key, equal to an ``ExportFormat`` or ``ImportFormat`` value.
        media_type: Media type of the files.
        extension: File name extension including the dot, such as ``.geojson``.
        supports_geometry: Whether the format carries geometry natively.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: FormatCode
    media_type: MediaTypeName
    extension: str = Field(pattern=EXTENSION_PATTERN)
    supports_geometry: bool


def _unique_codes(descriptors: tuple[FormatDescriptor, ...]) -> bool:
    return len({descriptor.code for descriptor in descriptors}) == len(descriptors)


class FormatRegistry(BaseModel):
    """The export and import formats Yakhnama knows, keyed by code.

    Immutable: ``with_export_format`` and ``with_import_format`` return a new
    registry, so registering a format never changes one another component holds.

    Implements: Registry.

    Attributes:
        export_formats: Descriptors of the formats exports can be written in.
        import_formats: Descriptors of the formats imports can be read from.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    export_formats: tuple[FormatDescriptor, ...] = Field(
        default=(), max_length=REGISTRY_MAX_FORMATS
    )
    import_formats: tuple[FormatDescriptor, ...] = Field(
        default=(), max_length=REGISTRY_MAX_FORMATS
    )

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        if not (
            _unique_codes(self.export_formats) and _unique_codes(self.import_formats)
        ):
            message = "a format code is registered at most once per direction"
            raise ValueError(message)
        return self

    def with_export_format(self, descriptor: FormatDescriptor) -> Self:
        """Return a registry that also knows ``descriptor`` for exports.

        Args:
            descriptor: The new format.

        Returns:
            The extended registry.

        Raises:
            ConflictError: If an export format with the same code exists.
        """
        _require_new(descriptor, self.export_formats, "export")
        return self.model_validate(
            {
                "export_formats": (*self.export_formats, descriptor),
                "import_formats": self.import_formats,
            }
        )

    def with_import_format(self, descriptor: FormatDescriptor) -> Self:
        """Return a registry that also knows ``descriptor`` for imports.

        Args:
            descriptor: The new format.

        Returns:
            The extended registry.

        Raises:
            ConflictError: If an import format with the same code exists.
        """
        _require_new(descriptor, self.import_formats, "import")
        return self.model_validate(
            {
                "export_formats": self.export_formats,
                "import_formats": (*self.import_formats, descriptor),
            }
        )

    def export_descriptor(self, code: str) -> FormatDescriptor:
        """Return the export format registered under ``code``.

        Args:
            code: A format code such as ``geojson``.

        Returns:
            Its descriptor.

        Raises:
            UnsupportedFormatError: If no export format has that code.
        """
        return _find(code, self.export_formats, "export")

    def import_descriptor(self, code: str) -> FormatDescriptor:
        """Return the import format registered under ``code``.

        Args:
            code: A format code such as ``csv``.

        Returns:
            Its descriptor.

        Raises:
            UnsupportedFormatError: If no import format has that code.
        """
        return _find(code, self.import_formats, "import")

    def is_export_supported(self, code: str) -> bool:
        """Tell whether exports can be written in ``code``.

        Args:
            code: A format code.

        Returns:
            ``True`` if an export format has that code.
        """
        return any(descriptor.code == code for descriptor in self.export_formats)

    def is_import_supported(self, code: str) -> bool:
        """Tell whether imports can be read from ``code``.

        Args:
            code: A format code.

        Returns:
            ``True`` if an import format has that code.
        """
        return any(descriptor.code == code for descriptor in self.import_formats)


def _require_new(
    descriptor: FormatDescriptor,
    known: Iterable[FormatDescriptor],
    direction: str,
) -> None:
    if any(existing.code == descriptor.code for existing in known):
        message = f"an {direction} format with this code is already registered"
        raise ConflictError(
            message, details={"format": descriptor.code, "direction": direction}
        )


def _find(
    code: str, known: Iterable[FormatDescriptor], direction: str
) -> FormatDescriptor:
    for descriptor in known:
        if descriptor.code == code:
            return descriptor
    raise UnsupportedFormatError.for_code(code, direction)


JSON_DESCRIPTOR: Final = FormatDescriptor(
    code=ExportFormat.JSON.value,
    media_type="application/json",
    extension=".json",
    supports_geometry=False,
)
GEOJSON_DESCRIPTOR: Final = FormatDescriptor(
    code=ExportFormat.GEOJSON.value,
    media_type="application/geo+json",
    extension=".geojson",
    supports_geometry=True,
)
CSV_DESCRIPTOR: Final = FormatDescriptor(
    code=ExportFormat.CSV.value,
    media_type="text/csv",
    extension=".csv",
    supports_geometry=False,
)
GEOPARQUET_DESCRIPTOR: Final = FormatDescriptor(
    code=ExportFormat.GEOPARQUET.value,
    media_type="application/vnd.apache.parquet",
    extension=".parquet",
    supports_geometry=True,
)

_EXPORT_DESCRIPTORS: Final = {
    ExportFormat.JSON: JSON_DESCRIPTOR,
    ExportFormat.GEOJSON: GEOJSON_DESCRIPTOR,
    ExportFormat.CSV: CSV_DESCRIPTOR,
    ExportFormat.GEOPARQUET: GEOPARQUET_DESCRIPTOR,
}
_IMPORT_DESCRIPTORS: Final = {
    ImportFormat.CSV: CSV_DESCRIPTOR,
    ImportFormat.GEOJSON: GEOJSON_DESCRIPTOR,
}

DEFAULT_FORMAT_REGISTRY: Final = FormatRegistry(
    export_formats=tuple(_EXPORT_DESCRIPTORS[code] for code in ExportFormat),
    import_formats=tuple(_IMPORT_DESCRIPTORS[code] for code in ImportFormat),
)
"""Every format this version supports; a new ``ExportFormat`` or ``ImportFormat``
member without a descriptor fails at import time (``KeyError``), never at run time."""
