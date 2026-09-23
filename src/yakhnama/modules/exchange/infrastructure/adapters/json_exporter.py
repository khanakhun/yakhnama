"""JSON export (format code ``json``): one array of row objects.

Each row is written as ``row.model_dump(mode="json")``: field names as in the export
row DTOs, UTC timestamps in ISO 8601, ids as text, a monetary amount as decimal
text (exact), a geometry as a GeoJSON object. The array is streamed row by row,
so the whole dataset is never held in memory.

Patterns: Strategy.
"""

import json
from collections.abc import AsyncIterator
from typing import Final

from yakhnama.modules.exchange.application.formats import BinarySink, ExportRow
from yakhnama.modules.exchange.domain.registry import JSON_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters.flat_layout import (
    require_dataset,
)

_ROW_SEPARATOR: Final = b",\n"


def encode_row(row: ExportRow) -> bytes:
    """Serialise one row as compact UTF-8 JSON.

    Args:
        row: The row.

    Returns:
        The JSON object, without a trailing newline.
    """
    return json.dumps(
        row.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class JsonExporter:
    """Writes a dataset as a JSON array of objects, streamed.

    Implements: Strategy (``Exporter``).
    """

    @property
    def format(self) -> ExportFormat:
        """Return ``json``."""
        return ExportFormat.JSON

    @property
    def media_type(self) -> str:
        """Return the descriptor's media type, ``application/json``."""
        return JSON_DESCRIPTOR.media_type

    async def write(
        self,
        dataset: ExportDataset,
        rows: AsyncIterator[ExportRow],
        sink: BinarySink,
    ) -> None:
        """Write ``[``, each row, ``]``.

        Args:
            dataset: The dataset every row belongs to.
            rows: The rows.
            sink: Where the bytes go.

        Raises:
            InvariantViolationError: If a row is of another dataset.
            ValidationError: If the sink refuses more bytes.
        """
        await sink.write(b"[\n")
        is_first = True
        async for row in rows:
            require_dataset(row, dataset)
            prefix = b"" if is_first else _ROW_SEPARATOR
            await sink.write(prefix + encode_row(row))
            is_first = False
        await sink.write(b"]\n" if is_first else b"\n]\n")
