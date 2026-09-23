"""GeoParquet export (format code ``geoparquet``): Parquet with GeoParquet 1.1 metadata.

The columns are the dataset's flat layout (``flat_layout``) with Arrow types
(text ``string``, lists ``list<string>``, integers ``int64``, floats ``float64``,
timestamps ``timestamp[us, UTC]``, a monetary amount as decimal ``string`` so it
stays exact), plus ``geometry``: the row's geometry (see
``flat_layout.geometry_of``) as ISO WKB ``binary``, null when the row has none.
Claims have no location, so their ``geometry`` column is all null (**proposed**,
see the open question on claim geometry).

Rows are written in record batches of ``BATCH_ROWS``, each one Parquet row group,
and the bytes each batch produces are forwarded to the sink at once, so memory is
bounded by one batch and the dataset is never held whole.

The file key-value metadata ``geo`` follows GeoParquet 1.1.0: ``version``,
``primary_column`` (``geometry``) and ``columns.geometry`` with ``encoding``
``WKB``, ``geometry_types`` (the sorted types actually written, empty when none)
and ``bbox`` (``[xmin, ymin, xmax, ymax]`` over every geometry, left out when
there is none). ``crs`` is deliberately **left out**: the specification defines an
absent ``crs`` as OGC:CRS84 (WGS84, longitude first), which is exactly Yakhnama's
CRS, whereas ``"crs": null`` would declare the CRS *unknown*. ``geo`` is known only
after the last row, so it is added to the footer on close, and the Arrow schema is
not embedded (``store_schema=False``): readers then take the schema metadata,
``geo`` included, from the file metadata (checked by the tests), and every column
type above maps back to the same Arrow type.

Patterns: Strategy, Adapter (a file-like object over the async sink).
"""

import io
import json
from collections.abc import AsyncIterator, Buffer
from decimal import Decimal
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq
import shapely.geometry

from yakhnama.modules.exchange.application.formats import BinarySink, ExportRow
from yakhnama.modules.exchange.domain.registry import GEOPARQUET_DESCRIPTOR
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

BATCH_ROWS: Final = 1000
GEOMETRY_COLUMN: Final = "geometry"
GEOPARQUET_VERSION: Final = "1.1.0"
GEO_METADATA_KEY: Final = "geo"

_ARROW_TYPES: Final[dict[ColumnKind, pa.DataType]] = {
    ColumnKind.TEXT: pa.string(),
    ColumnKind.TEXT_LIST: pa.list_(pa.string()),
    ColumnKind.INTEGER: pa.int64(),
    ColumnKind.FLOAT: pa.float64(),
    ColumnKind.DECIMAL: pa.string(),
    ColumnKind.TIMESTAMP: pa.timestamp("us", tz="UTC"),
}


def arrow_schema(layout: DatasetLayout) -> pa.Schema:
    """Return the Arrow schema of a dataset's GeoParquet file.

    Args:
        layout: The dataset's flat layout.

    Returns:
        One nullable field per flat column, then the ``geometry`` binary column.
    """
    fields = [
        pa.field(column.name, _ARROW_TYPES[column.kind]) for column in layout.columns
    ]
    return pa.schema([*fields, pa.field(GEOMETRY_COLUMN, pa.binary())])


def _arrow_value(value: FlatValue) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


class _ByteQueue(io.RawIOBase):
    """A write-only, file-like buffer the synchronous Parquet writer fills.

    ``take`` hands the bytes written so far to the async side, which forwards
    them to the sink; the position keeps counting so the writer's offsets stay
    right.

    Implements: Adapter (file object over the async ``BinarySink``).
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending = bytearray()
        self._position = 0

    def write(self, data: Buffer, /) -> int:
        """Queue ``data``; see ``io.RawIOBase.write``."""
        view = memoryview(data)
        self._pending.extend(view)
        self._position += view.nbytes
        return view.nbytes

    def tell(self) -> int:
        """Return how many bytes were written in total."""
        return self._position

    def writable(self) -> bool:
        """Return ``True``: the queue only accepts writes."""
        return True

    def take(self) -> bytes:
        """Return and forget the bytes written since the last call."""
        data = bytes(self._pending)
        self._pending.clear()
        return data


class _GeometryStatistics:
    """Collects the geometry types and bounding box written so far.

    Implements: Value Object (accumulator of the ``geo`` metadata).
    """

    def __init__(self) -> None:
        self.types: set[str] = set()
        self.bounds: list[float] | None = None

    def add(
        self, geometry_type: str, bounds: tuple[float, float, float, float]
    ) -> None:
        self.types.add(geometry_type)
        if self.bounds is None:
            self.bounds = list(bounds)
            return
        self.bounds = [
            min(self.bounds[0], bounds[0]),
            min(self.bounds[1], bounds[1]),
            max(self.bounds[2], bounds[2]),
            max(self.bounds[3], bounds[3]),
        ]

    def metadata(self) -> str:
        column: dict[str, object] = {
            "encoding": "WKB",
            "geometry_types": sorted(self.types),
        }
        if self.bounds is not None:
            column["bbox"] = self.bounds
        return json.dumps(
            {
                "version": GEOPARQUET_VERSION,
                "primary_column": GEOMETRY_COLUMN,
                "columns": {GEOMETRY_COLUMN: column},
            },
            separators=(",", ":"),
        )


class _BatchBuilder:
    """Collects rows column by column until a record batch is due.

    Implements: Value Object (accumulator of one record batch).
    """

    def __init__(self, layout: DatasetLayout, schema: pa.Schema) -> None:
        self._schema = schema
        self._columns: list[list[object]] = [[] for _ in range(len(schema))]
        self._layout = layout
        self.statistics = _GeometryStatistics()

    @property
    def size(self) -> int:
        return len(self._columns[0])

    def add(self, row: ExportRow) -> None:
        for index, value in enumerate(flat_values(row)):
            self._columns[index].append(_arrow_value(value))
        geometry = geometry_of(row)
        wkb: bytes | None = None
        if geometry is not None:
            shape = shapely.geometry.shape(geometry_mapping(geometry))
            wkb = shape.wkb
            self.statistics.add(geometry.type, shape.bounds)
        self._columns[-1].append(wkb)

    def take(self) -> pa.RecordBatch:
        arrays = [
            pa.array(values, type=field.type)
            for values, field in zip(self._columns, self._schema, strict=True)
        ]
        self._columns = [[] for _ in range(len(self._schema))]
        return pa.record_batch(arrays, schema=self._schema)


class GeoParquetExporter:
    """Writes a dataset as GeoParquet 1.1, one row group per batch of rows.

    Implements: Strategy (``Exporter``).

    Attributes:
        batch_rows: Rows per record batch and row group.
    """

    def __init__(self, batch_rows: int = BATCH_ROWS) -> None:
        """Create the exporter.

        Args:
            batch_rows: Rows per record batch; smaller in tests.
        """
        self.batch_rows = batch_rows

    @property
    def format(self) -> ExportFormat:
        """Return ``geoparquet``."""
        return ExportFormat.GEOPARQUET

    @property
    def media_type(self) -> str:
        """Return the descriptor's media type, ``application/vnd.apache.parquet``."""
        return GEOPARQUET_DESCRIPTOR.media_type

    async def write(
        self,
        dataset: ExportDataset,
        rows: AsyncIterator[ExportRow],
        sink: BinarySink,
    ) -> None:
        """Write the rows batch by batch, then the footer with ``geo``.

        Args:
            dataset: The dataset every row belongs to.
            rows: The rows.
            sink: Where the bytes go.

        Raises:
            InvariantViolationError: If a row is of another dataset.
            ValidationError: If the sink refuses more bytes.
        """
        layout = layout_of(dataset)
        schema = arrow_schema(layout)
        queue = _ByteQueue()
        writer = pq.ParquetWriter(
            pa.PythonFile(queue, mode="w"), schema, store_schema=False
        )
        builder = _BatchBuilder(layout, schema)
        try:
            async for row in rows:
                require_dataset(row, dataset)
                builder.add(row)
                if builder.size >= self.batch_rows:
                    writer.write_batch(builder.take())
                    await sink.write(queue.take())
            if builder.size:
                writer.write_batch(builder.take())
            writer.add_key_value_metadata(
                {GEO_METADATA_KEY: builder.statistics.metadata()}
            )
        finally:
            # Closing writes the footer into the queue; on failure those bytes
            # are simply never forwarded, so no partial file reaches the sink.
            writer.close()
        await sink.write(queue.take())
