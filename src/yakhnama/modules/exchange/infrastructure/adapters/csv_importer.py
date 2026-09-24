"""CSV import (format code ``csv``): RFC 4180 records tokenised as they stream in.

The file is decoded as UTF-8; a leading byte order mark is tolerated (spreadsheets
write one) and dropped. The first record is the header; every later record is one
data row, mapped cell by cell to the header's names. Nothing is validated here
beyond the file's structure: ``require_backfill_header`` and
``ImportedEventDraft.from_flat_row`` judge the content, and the handler counts and
caps the rows.

Why not ``csv.reader``: it pulls lines from a *synchronous* iterator, while the file
arrives from an async ``BinarySource``, so it would need the whole file in memory
or a thread; and its field size cap is process-wide state (``csv.field_size_limit``)
below the 1 MB ``geometry`` cell the contract allows. The tokeniser below keeps
only the current record in memory and reads the file chunk by chunk.

Dialect (RFC 4180): comma separator; records end in CRLF, LF or CR; a field may be
quoted with ``"``, and inside quotes ``""`` is one quote and line breaks are part
of the field. Stricter than RFC 4180's permissive readers, and deliberately so,
because the file is untrusted: a quote inside an unquoted field, text after a
closing quote, an unterminated quote, invalid UTF-8, a record with more cells than
the header, or a record longer than ``RECORD_MAX_CHARACTERS`` raises
``ImportContractError`` and fails the import as a whole. Blank lines are skipped
and do not count as data rows. A record with fewer cells than the header leaves the
remaining columns absent, which the contract allows.

Patterns: Strategy.
"""

import codecs
import re
import weakref
from collections.abc import AsyncIterator, Mapping
from enum import Enum, auto
from typing import Final

from yakhnama.modules.exchange.application.formats import BinarySource
from yakhnama.modules.exchange.domain.backfill import (
    BACKFILL_COLUMNS,
    CELL_MAX_LENGTH,
    GEOMETRY_CELL_MAX_LENGTH,
)
from yakhnama.modules.exchange.domain.errors import ImportContractError
from yakhnama.modules.exchange.domain.value_objects import ImportFormat

READ_CHUNK_BYTES: Final = 64 * 1024

RECORD_MAX_CHARACTERS: Final = 2 * (
    GEOMETRY_CELL_MAX_LENGTH + len(BACKFILL_COLUMNS) * CELL_MAX_LENGTH
)
"""Longest record read, counted in raw characters: every contract cell at its
maximum length with every character a doubled quote. A longer record could not be
a valid row, so it is refused before it fills memory."""

# ``details["reason"]`` of each structural error; fixed slugs, never file text.
ENCODING_REASON: Final = "encoding"
RECORD_TOO_LONG_REASON: Final = "record_too_long"
QUOTE_IN_UNQUOTED_FIELD_REASON: Final = "quote_in_unquoted_field"
TEXT_AFTER_CLOSING_QUOTE_REASON: Final = "text_after_closing_quote"
UNTERMINATED_QUOTE_REASON: Final = "unterminated_quote"
MORE_CELLS_THAN_HEADER_REASON: Final = "more_cells_than_header"
NO_HEADER_REASON: Final = "no_header"

_UNQUOTED_STOP: Final = re.compile(r'[,\r\n"]')
_QUOTE: Final = '"'
_SEPARATOR: Final = ","
_LINE_BREAKS: Final = frozenset({"\r", "\n"})


class _State(Enum):
    """Where the tokeniser is inside a record.

    Implements: Value Object.
    """

    RECORD_START = auto()
    FIELD_START = auto()
    UNQUOTED = auto()
    QUOTED = auto()
    QUOTE_IN_QUOTED = auto()


def _contract_error(reason: str) -> ImportContractError:
    # The reason is a fixed slug; no text of the file is ever quoted.
    return ImportContractError(
        "the file is not well-formed CSV", details={"reason": reason}
    )


class CsvRecordReader:
    """Reads RFC 4180 records one at a time from an async byte source.

    Implements: Adapter (tokeniser over ``BinarySource``).
    """

    def __init__(
        self, source: BinarySource, chunk_bytes: int = READ_CHUNK_BYTES
    ) -> None:
        """Create the reader; nothing is read yet.

        Args:
            source: The file.
            chunk_bytes: Bytes asked for per read.
        """
        self._source = source
        self._chunk_bytes = chunk_bytes
        # utf-8-sig drops one leading byte order mark and nothing else.
        self._decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self._text = ""
        self._position = 0
        self._is_exhausted = False
        self._skip_line_feed = False
        self._record_characters = 0

    async def _fill(self) -> bool:
        """Append the next chunk of text; return ``False`` at the end of the file."""
        while not self._is_exhausted:
            data = await self._source.read(self._chunk_bytes)
            self._is_exhausted = not data
            try:
                text = self._decoder.decode(data, final=self._is_exhausted)
            except UnicodeDecodeError as error:
                raise _contract_error(ENCODING_REASON) from error
            if text:
                self._text = self._text[self._position :] + text
                self._position = 0
                return True
        return False

    def _advance(self, position: int) -> None:
        self._record_characters += position - self._position
        if self._record_characters > RECORD_MAX_CHARACTERS:
            raise _contract_error(RECORD_TOO_LONG_REASON)
        self._position = position

    async def next_record(self) -> list[str] | None:
        """Return the next non-blank record's cells.

        Returns:
            The cells in file order, or ``None`` at the end of the file.

        Raises:
            ImportContractError: If the file breaks the dialect described in the
                module documentation.
        """
        fields: list[str] = []
        pieces: list[str] = []
        state = _State.RECORD_START
        self._record_characters = 0
        while True:
            if self._position >= len(self._text) and not await self._fill():
                return self._finish(state, fields, pieces)
            if self._skip_line_feed:
                # A CR ended the previous record; a LF right after it belongs to
                # the same line break, even when it arrives in the next chunk.
                self._skip_line_feed = False
                if self._text[self._position] == "\n":
                    self._advance(self._position + 1)
                    continue
            state, is_complete = self._step(state, fields, pieces)
            if is_complete:
                return fields

    def _step(
        self, state: _State, fields: list[str], pieces: list[str]
    ) -> tuple[_State, bool]:
        """Consume text from the current position; return the new state.

        The second value is ``True`` once a record ended at a line break.
        """
        if state is _State.QUOTED:
            return self._step_quoted(pieces)
        if state is _State.UNQUOTED:
            return self._step_unquoted(fields, pieces)
        if state is _State.QUOTE_IN_QUOTED:
            return self._step_after_quote(fields, pieces)
        return self._step_field_start(state, fields, pieces)

    def _step_quoted(self, pieces: list[str]) -> tuple[_State, bool]:
        text = self._text
        end = text.find(_QUOTE, self._position)
        if end < 0:
            pieces.append(text[self._position :])
            self._advance(len(text))
            return _State.QUOTED, False
        pieces.append(text[self._position : end])
        self._advance(end + 1)
        return _State.QUOTE_IN_QUOTED, False

    def _step_unquoted(
        self, fields: list[str], pieces: list[str]
    ) -> tuple[_State, bool]:
        text = self._text
        match = _UNQUOTED_STOP.search(text, self._position)
        if match is None:
            pieces.append(text[self._position :])
            self._advance(len(text))
            return _State.UNQUOTED, False
        pieces.append(text[self._position : match.start()])
        self._advance(match.start())
        if match.group() == _QUOTE:
            raise _contract_error(QUOTE_IN_UNQUOTED_FIELD_REASON)
        return self._delimit(fields, pieces)

    def _step_after_quote(
        self, fields: list[str], pieces: list[str]
    ) -> tuple[_State, bool]:
        character = self._text[self._position]
        if character == _QUOTE:
            # A doubled quote inside a quoted field stands for one quote.
            pieces.append(_QUOTE)
            self._advance(self._position + 1)
            return _State.QUOTED, False
        if character != _SEPARATOR and character not in _LINE_BREAKS:
            raise _contract_error(TEXT_AFTER_CLOSING_QUOTE_REASON)
        return self._delimit(fields, pieces)

    def _step_field_start(
        self, state: _State, fields: list[str], pieces: list[str]
    ) -> tuple[_State, bool]:
        character = self._text[self._position]
        if state is _State.RECORD_START and character in _LINE_BREAKS:
            # A blank line: skipped, never a data row.
            self._end_line(character)
            return state, False
        if character == _QUOTE:
            self._advance(self._position + 1)
            return _State.QUOTED, False
        if character == _SEPARATOR or character in _LINE_BREAKS:
            return self._delimit(fields, pieces)
        return _State.UNQUOTED, False

    def _delimit(self, fields: list[str], pieces: list[str]) -> tuple[_State, bool]:
        """Close the current field at a separator or a line break."""
        fields.append("".join(pieces))
        pieces.clear()
        character = self._text[self._position]
        if character == _SEPARATOR:
            self._advance(self._position + 1)
            return _State.FIELD_START, False
        self._end_line(character)
        return _State.RECORD_START, True

    def _end_line(self, character: str) -> None:
        self._advance(self._position + 1)
        self._skip_line_feed = character == "\r"

    @staticmethod
    def _finish(
        state: _State, fields: list[str], pieces: list[str]
    ) -> list[str] | None:
        """Close the last record at the end of the file, if it has content."""
        if state is _State.RECORD_START:
            return None
        if state is _State.QUOTED:
            raise _contract_error(UNTERMINATED_QUOTE_REASON)
        fields.append("".join(pieces))
        return fields


class CsvImporter:
    """Tokenises a CSV file into the header and ``(row_number, cells)`` rows.

    One instance serves every import: the reader of each source lives in a
    weak-keyed map between ``header`` and ``read``, so concurrent imports never
    share state and a source that is dropped takes its reader with it.

    Implements: Strategy (``Importer``).
    """

    def __init__(self, chunk_bytes: int = READ_CHUNK_BYTES) -> None:
        """Create the importer.

        Args:
            chunk_bytes: Bytes asked for per read; smaller in tests.
        """
        self._chunk_bytes = chunk_bytes
        self._open: weakref.WeakKeyDictionary[
            BinarySource, tuple[CsvRecordReader, tuple[str, ...]]
        ] = weakref.WeakKeyDictionary()

    @property
    def format(self) -> ImportFormat:
        """Return ``csv``."""
        return ImportFormat.CSV

    async def header(self, source: BinarySource) -> tuple[str, ...]:
        """Read the header record.

        Args:
            source: The file, not read yet.

        Returns:
            The column names in file order, exactly as written.

        Raises:
            ImportContractError: If the file is empty or its first record is
                malformed.
        """
        reader = CsvRecordReader(source, self._chunk_bytes)
        names = await reader.next_record()
        if names is None:
            message = "the file has no header row"
            raise ImportContractError(message, details={"reason": NO_HEADER_REASON})
        header = tuple(names)
        self._open[source] = (reader, header)
        return header

    async def read(
        self, source: BinarySource
    ) -> AsyncIterator[tuple[int, Mapping[str, str]]]:
        """Yield every data row after the header.

        Args:
            source: The source ``header`` read; if ``header`` was not called, the
                header is read first.

        Yields:
            ``(row_number, cells)`` with rows numbered from 1; a short record maps
            only its leading columns.

        Raises:
            ImportContractError: If a record is malformed or has more cells than
                the header.
        """
        if source not in self._open:
            await self.header(source)
        reader, header = self._open[source]
        try:
            row_number = 0
            while (record := await reader.next_record()) is not None:
                if len(record) > len(header):
                    raise _contract_error(MORE_CELLS_THAN_HEADER_REASON)
                row_number += 1
                yield row_number, dict(zip(header, record, strict=False))
        finally:
            self._open.pop(source, None)
