"""Unit tests of the CSV importer and its streaming RFC 4180 tokeniser."""

import asyncio
import csv
import io

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.fakes.exchange import csv_bytes
from tests.unit.modules.exchange.domain.test_backfill import drafts
from tests.unit.modules.exchange.infrastructure.adapters.support import (
    ChunkedSource,
    import_rows,
)
from yakhnama.modules.exchange.domain.backfill import (
    BACKFILL_COLUMNS,
    ImportedEventDraft,
    require_backfill_header,
)
from yakhnama.modules.exchange.domain.errors import ImportContractError
from yakhnama.modules.exchange.domain.value_objects import ImportFormat
from yakhnama.modules.exchange.infrastructure.adapters import csv_importer
from yakhnama.modules.exchange.infrastructure.adapters.csv_importer import (
    ENCODING_REASON,
    MORE_CELLS_THAN_HEADER_REASON,
    NO_HEADER_REASON,
    QUOTE_IN_UNQUOTED_FIELD_REASON,
    RECORD_TOO_LONG_REASON,
    TEXT_AFTER_CLOSING_QUOTE_REASON,
    UNTERMINATED_QUOTE_REASON,
    CsvImporter,
    CsvRecordReader,
)


async def _records(data: bytes, chunk_bytes: int = 3) -> list[list[str]]:
    reader = CsvRecordReader(ChunkedSource(data), chunk_bytes)
    records: list[list[str]] = []
    while (record := await reader.next_record()) is not None:
        records.append(record)
    return records


def _write(records: list[list[str]], terminator: str = "\r\n") -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator=terminator).writerows(records)
    return buffer.getvalue().encode("utf-8")


def test_csv_importer_declares_csv_format() -> None:
    importer = CsvImporter()

    code = importer.format

    assert code is ImportFormat.CSV


async def test_csv_importer_reads_header_then_numbered_rows() -> None:
    data = b"title,hazard_type\r\nFirst,glof\r\nSecond,landslide\r\n"

    header, rows = await import_rows(CsvImporter(), data)

    assert header == ("title", "hazard_type")
    assert rows == [
        (1, {"title": "First", "hazard_type": "glof"}),
        (2, {"title": "Second", "hazard_type": "landslide"}),
    ]


async def test_csv_importer_tolerates_byte_order_mark() -> None:
    data = "﻿title,summary\nA,B\n".encode()

    header, rows = await import_rows(CsvImporter(chunk_bytes=1), data)

    assert header == ("title", "summary")
    assert rows == [(1, {"title": "A", "summary": "B"})]


async def test_csv_importer_reads_quotes_commas_newlines_and_urdu() -> None:
    title = 'Flood at "Upper" valley, گلگت'
    summary = "line one\r\nline two\nline three\rend"
    data = _write([["title", "summary"], [title, summary]])

    _, rows = await import_rows(CsvImporter(chunk_bytes=1), data)

    assert rows == [(1, {"title": title, "summary": summary})]


@pytest.mark.parametrize("terminator", ["\r\n", "\n", "\r"])
async def test_csv_record_reader_accepts_every_line_break(terminator: str) -> None:
    data = terminator.join(["a,b", "c,d", ""]).encode()

    records = await _records(data, chunk_bytes=1)

    assert records == [["a", "b"], ["c", "d"]]


async def test_csv_record_reader_reads_last_record_without_line_break() -> None:
    records = await _records(b'a,b\r\nc,"d"')

    assert records == [["a", "b"], ["c", "d"]]


async def test_csv_record_reader_skips_blank_lines_and_keeps_empty_fields() -> None:
    records = await _records(b'\r\n\r\na,,b\n\n,\n""\n')

    assert records == [["a", "", "b"], ["", ""], [""]]


async def test_csv_importer_short_record_leaves_trailing_columns_absent() -> None:
    _, rows = await import_rows(CsvImporter(), b"a,b,c\n1\n")

    assert rows == [(1, {"a": "1"})]


async def test_csv_importer_empty_file_raises_no_header() -> None:
    with pytest.raises(ImportContractError) as caught:
        await import_rows(CsvImporter(), b"\r\n\r\n")

    assert caught.value.details["reason"] == NO_HEADER_REASON


async def test_csv_importer_read_without_header_call_reads_header_first() -> None:
    importer = CsvImporter()
    source = ChunkedSource(b"a,b\n1,2\n")

    rows = [row async for row in importer.read(source)]

    assert rows == [(1, {"a": "1", "b": "2"})]


async def test_csv_importer_keeps_concurrent_sources_apart() -> None:
    importer = CsvImporter(chunk_bytes=2)
    first = ChunkedSource(b"a\n1\n2\n")
    second = ChunkedSource(b"b\nx\n")
    await importer.header(first)
    await importer.header(second)

    second_rows = [row async for row in importer.read(second)]
    first_rows = [row async for row in importer.read(first)]

    assert second_rows == [(1, {"b": "x"})]
    assert first_rows == [(1, {"a": "1"}), (2, {"a": "2"})]


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b'a,b\nx"y,z\n', QUOTE_IN_UNQUOTED_FIELD_REASON),
        (b'a,b\n"x"y,z\n', TEXT_AFTER_CLOSING_QUOTE_REASON),
        (b'a,b\n"x,z\n', UNTERMINATED_QUOTE_REASON),
        (b"a,b\n\xff\xfe,z\n", ENCODING_REASON),
        (b"a,b\n\xc3", ENCODING_REASON),
        (b"a,b\n1,2,3\n", MORE_CELLS_THAN_HEADER_REASON),
    ],
)
async def test_csv_importer_malformed_file_raises_contract_error(
    data: bytes, reason: str
) -> None:
    with pytest.raises(ImportContractError) as caught:
        await import_rows(CsvImporter(chunk_bytes=2), data)

    assert caught.value.details == {"reason": reason}
    assert "x" not in caught.value.message


async def test_csv_importer_record_too_long_raises_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(csv_importer, "RECORD_MAX_CHARACTERS", 10)

    with pytest.raises(ImportContractError) as caught:
        await import_rows(CsvImporter(), b"a,b\n12345,678901\n")

    assert caught.value.details == {"reason": RECORD_TOO_LONG_REASON}


async def test_csv_importer_record_at_limit_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(csv_importer, "RECORD_MAX_CHARACTERS", 10)

    _, rows = await import_rows(CsvImporter(), b"a,b\n1234,5678\n")

    assert rows == [(1, {"a": "1234", "b": "5678"})]


def test_record_max_characters_fits_the_largest_contract_row() -> None:
    limit = csv_importer.RECORD_MAX_CHARACTERS

    assert limit == 2 * (1_000_000 + len(BACKFILL_COLUMNS) * 10_000)


FIELDS = st.text(
    alphabet=st.one_of(
        st.characters(codec="utf-8"), st.sampled_from([",", '"', "\r", "\n", "گ"])
    ),
    max_size=12,
)


@settings(max_examples=150)
@given(
    records=st.lists(
        st.lists(FIELDS, min_size=1, max_size=5).filter(lambda cells: cells != [""]),
        max_size=6,
    ),
    chunk_bytes=st.integers(1, 17),
    terminator=st.sampled_from(["\r\n", "\n"]),
)
def test_csv_record_reader_reads_what_the_standard_writer_wrote(
    records: list[list[str]], chunk_bytes: int, terminator: str
) -> None:
    data = _write(records, terminator)

    read = asyncio.run(_records(data, chunk_bytes))

    assert read == records


@settings(max_examples=60, deadline=None)
@given(
    draft_list=st.lists(drafts(), min_size=1, max_size=4),
    chunk_bytes=st.integers(1, 64),
)
def test_csv_import_of_exported_drafts_returns_equal_drafts(
    draft_list: list[ImportedEventDraft], chunk_bytes: int
) -> None:
    data = csv_bytes([draft.to_flat_row() for draft in draft_list], BACKFILL_COLUMNS)

    header, rows = asyncio.run(import_rows(CsvImporter(chunk_bytes), data))

    require_backfill_header(header)
    restored = [
        ImportedEventDraft.from_flat_row(number, cells) for number, cells in rows
    ]
    assert restored == draft_list
