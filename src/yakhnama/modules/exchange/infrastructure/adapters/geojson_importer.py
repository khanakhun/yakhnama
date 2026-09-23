"""GeoJSON import (format code ``geojson``): one RFC 7946 ``FeatureCollection``.

Each feature is one data row: its ``properties`` become the cells, and its
``geometry`` is serialised back to compact GeoJSON text under the ``geometry``
column, which ``ImportedEventDraft.from_flat_row`` parses and validates. The header
is every property name in order of first appearance, then ``geometry``; a property
itself named ``geometry`` therefore appears twice and the handler's header check
refuses the file.

A property value becomes text as follows: a string as is; ``true``/``false`` as
those words; an integer in decimal; a float with ``repr`` (lossless); ``null`` as
an absent cell; an array or object as compact JSON (the contract has no nested
cells, so the row reports that column).

The collection is parsed whole: streaming JSON needs a dependency this module does
not have, and the handler's ``MeteredSource`` already caps the bytes at the size
declared for the import. Parsing is strict because the file is untrusted: invalid
UTF-8 or JSON, ``NaN`` or ``Infinity``, an object with a repeated key, or a
structure that is not a ``FeatureCollection`` of ``Feature`` objects raises
``ImportContractError``. A leading byte order mark is tolerated.

Patterns: Strategy.
"""

import json
import weakref
from collections.abc import AsyncIterator, Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.exchange.application.formats import BinarySource
from yakhnama.modules.exchange.domain.errors import ImportContractError
from yakhnama.modules.exchange.domain.value_objects import ImportFormat

READ_CHUNK_BYTES: Final = 64 * 1024
GEOMETRY_COLUMN: Final = "geometry"

# ``details["reason"]`` of each structural error; fixed slugs, never file text.
ENCODING_REASON: Final = "encoding"
JSON_REASON: Final = "json"
NOT_A_FEATURE_COLLECTION_REASON: Final = "not_a_feature_collection"
NOT_A_FEATURE_REASON: Final = "not_a_feature"

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)


def _contract_error(
    reason: str, feature_number: int | None = None
) -> ImportContractError:
    details: dict[str, object] = {"reason": reason}
    if feature_number is not None:
        details["feature_number"] = feature_number
    return ImportContractError(
        "the file is not a valid GeoJSON import", details=details
    )


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = dict(pairs)
    if len(result) != len(pairs):
        # A repeated key would let two readers of the same file see two values.
        message = "repeated key"
        raise ValueError(message)
    return result


def _reject_constant(name: str) -> float:
    message = f"non-finite number {name}"
    raise ValueError(message)


def parse_json(data: bytes) -> JsonValue:
    """Parse untrusted JSON strictly.

    Args:
        data: The whole file.

    Returns:
        The parsed value.

    Raises:
        ImportContractError: If the bytes are not UTF-8, not JSON, contain
            ``NaN``/``Infinity`` or repeat a key within an object.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise _contract_error(ENCODING_REASON) from error
    try:
        parsed: JsonValue = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError) as error:
        raise _contract_error(JSON_REASON) from error
    return parsed


def cell_text(value: JsonValue) -> str | None:
    """Return the cell text of one property value.

    Args:
        value: The value.

    Returns:
        Its text, or ``None`` for ``null`` (an absent cell).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ParsedFeature(BaseModel):
    """One feature's cells and property names, in file order.

    Implements: Value Object.

    Attributes:
        property_names: The feature's property names, ``null`` values included.
        cells: The row's cells: non-null properties as text, then ``geometry``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    property_names: tuple[str, ...]
    cells: dict[str, str]


def parse_features(document: JsonValue) -> list[ParsedFeature]:
    """Check the collection's structure and turn every feature into cells.

    Args:
        document: The parsed file.

    Returns:
        One parsed feature per feature, in file order.

    Raises:
        ImportContractError: If the document is not a ``FeatureCollection`` of
            ``Feature`` objects whose ``properties`` and ``geometry`` are objects
            or ``null``.
    """
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise _contract_error(NOT_A_FEATURE_COLLECTION_REASON)
    features = document.get("features")
    if not isinstance(features, list):
        raise _contract_error(NOT_A_FEATURE_COLLECTION_REASON)
    return [
        _parse_feature(feature, number) for number, feature in enumerate(features, 1)
    ]


def _parse_feature(feature: JsonValue, number: int) -> ParsedFeature:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise _contract_error(NOT_A_FEATURE_REASON, number)
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict | None) or not isinstance(geometry, dict | None):
        raise _contract_error(NOT_A_FEATURE_REASON, number)
    properties = properties or {}
    cells: dict[str, str] = {}
    for name, value in properties.items():
        text = cell_text(value)
        if text is not None:
            cells[name] = text
    if geometry is not None:
        # Set after the properties on purpose: a property named geometry is also
        # in the header twice, so the handler refuses the file before any row.
        cells[GEOMETRY_COLUMN] = json.dumps(
            geometry, ensure_ascii=False, separators=(",", ":")
        )
    return ParsedFeature(property_names=tuple(properties), cells=cells)


def header_of(features: list[ParsedFeature]) -> tuple[str, ...]:
    """Return the header of a parsed collection.

    Args:
        features: The parsed features.

    Returns:
        Every property name in order of first appearance, then ``geometry``.
    """
    names: dict[str, None] = {}
    for feature in features:
        names.update(dict.fromkeys(feature.property_names))
    return (*names, GEOMETRY_COLUMN)


class GeoJsonImporter:
    """Parses a GeoJSON ``FeatureCollection`` into the header and data rows.

    One instance serves every import: the features parsed by ``header`` are kept
    in a weak-keyed map until ``read`` has yielded them, so concurrent imports
    never share state.

    Implements: Strategy (``Importer``).
    """

    def __init__(self, chunk_bytes: int = READ_CHUNK_BYTES) -> None:
        """Create the importer.

        Args:
            chunk_bytes: Bytes asked for per read.
        """
        self._chunk_bytes = chunk_bytes
        self._parsed: weakref.WeakKeyDictionary[BinarySource, list[ParsedFeature]] = (
            weakref.WeakKeyDictionary()
        )

    @property
    def format(self) -> ImportFormat:
        """Return ``geojson``."""
        return ImportFormat.GEOJSON

    async def header(self, source: BinarySource) -> tuple[str, ...]:
        """Read and parse the whole collection, then return its header.

        Args:
            source: The file, not read yet.

        Returns:
            Every property name in order of first appearance, then ``geometry``.

        Raises:
            ImportContractError: If the file is not a valid collection.
            ValidationError: If the source refuses to hand out more bytes.
        """
        content = bytearray()
        while chunk := await source.read(self._chunk_bytes):
            content.extend(chunk)
        features = parse_features(parse_json(bytes(content)))
        self._parsed[source] = features
        return header_of(features)

    async def read(
        self, source: BinarySource
    ) -> AsyncIterator[tuple[int, Mapping[str, str]]]:
        """Yield one row per feature.

        Args:
            source: The source ``header`` read; if ``header`` was not called, the
                file is parsed first.

        Yields:
            ``(row_number, cells)`` with features numbered from 1.

        Raises:
            ImportContractError: If the file is not a valid collection.
        """
        if source not in self._parsed:
            await self.header(source)
        features = self._parsed.pop(source)
        for number, feature in enumerate(features, 1):
            yield number, feature.cells
