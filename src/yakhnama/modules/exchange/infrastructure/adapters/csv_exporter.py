"""CSV export (format code ``csv``): RFC 4180, UTF-8 without a byte order mark.

The header is the dataset's flat layout (``flat_layout``), written also for an
empty export; records end in CRLF and a cell is quoted only when it holds a comma,
a quote or a line break. Values: UTC timestamps in ISO 8601, floats with ``repr``
(lossless), a monetary amount as decimal text, list columns joined with ``;``, an
absent value as an empty cell. A spatial dataset gets a last column ``geometry``
with the row's geometry as compact GeoJSON (see ``flat_layout.geometry_of``).

**Spreadsheet formula injection.** A spreadsheet opening a CSV runs a cell that
starts with ``=``, ``+``, ``-`` or ``@`` (and, in some, a tab or carriage return)
as a formula, and free text in an export comes from contributors. Every *text*
cell that starts with one of those characters is therefore written with a
leading apostrophe (``'=1+1``), the OWASP recommendation: spreadsheets show the
text instead of evaluating it, and a reader of the raw file can strip exactly one
leading apostrophe from such a cell. Number, timestamp and GeoJSON cells are never
prefixed: they are produced from typed values (a negative longitude is a number,
not a formula), so the prefix would only corrupt them.

Patterns: Strategy.
"""

import csv
import io
import json
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from typing import Final

from yakhnama.modules.exchange.application.formats import BinarySink, ExportRow
from yakhnama.modules.exchange.domain.backfill import PLACE_CODE_SEPARATOR
from yakhnama.modules.exchange.domain.registry import CSV_DESCRIPTOR
from yakhnama.modules.exchange.domain.value_objects import ExportDataset, ExportFormat
from yakhnama.modules.exchange.infrastructure.adapters.flat_layout import (
    ColumnKind,
    DatasetLayout,
    FlatValue,
    flat_values,
    geometry_mapping,
    geometry_of,
    layout_of,
    require_dataset,
)

GEOMETRY_COLUMN: Final = "geometry"
FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
"""Leading characters that make a spreadsheet evaluate a cell."""
FORMULA_GUARD: Final = "'"
LIST_SEPARATOR: Final = PLACE_CODE_SEPARATOR
RECORD_TERMINATOR: Final = "\r\n"
_TEXT_KINDS: Final = frozenset({ColumnKind.TEXT, ColumnKind.TEXT_LIST})


def defuse_formula(text: str) -> str:
    """Prefix a text cell that a spreadsheet would evaluate as a formula.

    Args:
        text: The cell text.

    Returns:
        ``text`` with a leading apostrophe if it starts with one of
        ``FORMULA_PREFIXES``, otherwise ``text`` unchanged.
    """
    if text.startswith(FORMULA_PREFIXES):
        return FORMULA_GUARD + text
    return text


def cell_text(value: FlatValue) -> str:
    """Return the CSV text of one flat value, before any formula guard.

    Args:
        value: The value.

    Returns:
        Its text; empty for ``None``.
    """
    if value is None:
        return ""
    if isinstance(value, tuple):
        return LIST_SEPARATOR.join(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def header_of(layout: DatasetLayout) -> tuple[str, ...]:
    """Return the CSV header of a dataset.

    Args:
        layout: The dataset's flat layout.

    Returns:
        The flat column names, then ``geometry`` for a spatial dataset.
    """
    if layout.has_geometry:
        return (*layout.names, GEOMETRY_COLUMN)
    return layout.names


def record_of(row: ExportRow, layout: DatasetLayout) -> tuple[str, ...]:
    """Return the CSV cells of one row, formula guards applied.

    Args:
        row: The row.
        layout: Its dataset's flat layout.

    Returns:
        One cell per column of ``header_of(layout)``.
    """
    cells = [
        defuse_formula(cell_text(value))
        if column.kind in _TEXT_KINDS
        else cell_text(value)
        for column, value in zip(layout.columns, flat_values(row), strict=True)
    ]
    if layout.has_geometry:
        geometry = geometry_of(row)
        cells.append(
            ""
            if geometry is None
            else json.dumps(geometry_mapping(geometry), separators=(",", ":"))
        )
    return tuple(cells)


def _encode(records: Iterable[Iterable[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator=RECORD_TERMINATOR).writerows(records)
    return buffer.getvalue().encode("utf-8")


class CsvExporter:
    """Writes a dataset as RFC 4180 CSV, streamed record by record.

    Implements: Strategy (``Exporter``).
    """

    @property
    def format(self) -> ExportFormat:
        """Return ``csv``."""
        return ExportFormat.CSV

    @property
    def media_type(self) -> str:
        """Return the descriptor's media type, ``text/csv``."""
        return CSV_DESCRIPTOR.media_type

    async def write(
        self,
        dataset: ExportDataset,
        rows: AsyncIterator[ExportRow],
        sink: BinarySink,
    ) -> None:
        """Write the header, then one record per row.

        Args:
            dataset: The dataset every row belongs to.
            rows: The rows.
            sink: Where the bytes go.

        Raises:
            InvariantViolationError: If a row is of another dataset.
            ValidationError: If the sink refuses more bytes.
        """
        layout = layout_of(dataset)
        await sink.write(_encode([header_of(layout)]))
        async for row in rows:
            require_dataset(row, dataset)
            await sink.write(_encode([record_of(row, layout)]))
