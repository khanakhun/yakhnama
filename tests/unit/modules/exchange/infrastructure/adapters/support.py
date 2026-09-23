"""Byte sinks and sources for the format adapter tests, all in memory.

Patterns: Fake.
"""

from collections.abc import AsyncIterator, Iterable, Mapping, Sequence

from yakhnama.modules.exchange.application.formats import (
    Exporter,
    ExportRow,
    Importer,
)
from yakhnama.modules.exchange.domain.value_objects import ExportDataset


class RecordingSink:
    """``BinarySink`` keeping every write separately.

    Implements: Fake (of ``BinarySink``).

    Attributes:
        writes: Every chunk written, in order.
    """

    def __init__(self) -> None:
        """Create an empty sink."""
        self.writes: list[bytes] = []

    async def write(self, data: bytes) -> None:
        """Record ``data``.

        Args:
            data: The next bytes.
        """
        self.writes.append(data)

    @property
    def content(self) -> bytes:
        """Return every byte written, joined."""
        return b"".join(self.writes)


class ChunkedSource:
    """``BinarySource`` handing out at most ``chunk_bytes`` per read.

    Implements: Fake (of ``BinarySource``).
    """

    def __init__(self, data: bytes, chunk_bytes: int | None = None) -> None:
        """Create the source.

        Args:
            data: The file.
            chunk_bytes: The most bytes per read, whatever the caller asks for.
        """
        self._data = data
        self._chunk_bytes = chunk_bytes
        self._position = 0

    async def read(self, size: int) -> bytes:
        """Return the next bytes.

        Args:
            size: The most bytes the caller wants.

        Returns:
            Up to ``size`` (and ``chunk_bytes``) bytes; empty at the end.
        """
        limit = size if self._chunk_bytes is None else min(size, self._chunk_bytes)
        chunk = self._data[self._position : self._position + limit]
        self._position += len(chunk)
        return chunk


async def rows_of[RowT: ExportRow](rows: Iterable[RowT]) -> AsyncIterator[ExportRow]:
    """Yield ``rows`` asynchronously, as the export handler does.

    Args:
        rows: The rows.

    Yields:
        Each row.
    """
    for row in rows:
        yield row


async def export_bytes(
    exporter: Exporter, dataset: ExportDataset, rows: Sequence[ExportRow]
) -> RecordingSink:
    """Run an exporter over ``rows`` into a fresh sink.

    Args:
        exporter: The strategy.
        dataset: The dataset.
        rows: The rows.

    Returns:
        The sink with every write.
    """
    sink = RecordingSink()
    await exporter.write(dataset, rows_of(rows), sink)
    return sink


async def import_rows(
    importer: Importer, data: bytes, chunk_bytes: int | None = None
) -> tuple[tuple[str, ...], list[tuple[int, Mapping[str, str]]]]:
    """Run an importer over ``data``: header first, then every row.

    Args:
        importer: The strategy.
        data: The file.
        chunk_bytes: The most bytes the source hands out per read.

    Returns:
        The header and the ``(row_number, cells)`` rows.
    """
    source = ChunkedSource(data, chunk_bytes)
    header = await importer.header(source)
    rows = [row async for row in importer.read(source)]
    return header, rows
