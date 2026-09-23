---
name: add-exchange-format
description: Add an import or export format for the open dataset - an Importer or Exporter class registered by format code (never touching existing formats), a dry-run capable row-level validation report for importers, a metadata sidecar for exporters, and a round-trip property test.
---

# add-exchange-format

## When to use

- Researchers or partners need the dataset in a new format (CSV, GeoJSON, GeoPackage,
  a DesInventar-compatible layout, ...), or Yakhnama must accept bulk data in one.

## Preconditions

- The exchange base exists in `src/yakhnama/modules/exchange/application/formats.py`
  (integration-engineer). This skill assumes: `Exporter` Protocol (`format_code`,
  `export(rows, metadata) -> ExportArtifact`), `Importer` Protocol (`format_code`,
  `parse(content) -> ImportReport`), `ExchangeFormatRegistry` with `register_exporter` /
  `register_importer` that raise on a duplicate code, `ExportMetadata` (`licence`,
  `generated_at`, `filters`, `schema_version`, `citation`), `ImportReport` of
  `RowResult(row_number, row, errors)`, and the exchange row model (here
  `EventExchangeRow`, example only). The import handler around importers owns `dry_run`,
  per-batch atomicity and lineage. If the base differs, the base wins and this skill must
  be corrected.
- The dataset licence and citation text are decided by the maintainer; until then the
  sidecar carries the placeholder from settings, never a guessed licence.
- Any third-party library the format needs is added by the lead with `poetry add`, and
  reviewed by `security-reviewer`.

## Owning subagent

`integration-engineer`. The registration line goes in the composition root
(`platform/container.py`, architect).

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `src/yakhnama/modules/exchange/infrastructure/formats/<dataset>_<format>.py` | create | `...Exporter` and/or `...Importer` |
| `src/yakhnama/platform/container.py` | modify (architect) | `registry.register_exporter(...)` / `register_importer(...)` |
| `tests/unit/modules/exchange/infrastructure/formats/test_<dataset>_<format>.py` | create | Round trip, sidecar, row report, errors |
| `docs/architecture/data-formats.md` | modify | Format description, columns, units, version |
| `__init__.py` in every new package directory under `src/` and `tests/` (`infrastructure/formats/` and its test mirror) | create if absent | One-line module docstring; required by the structural test |

Open/Closed: adding a format creates one file and adds registration lines. It never edits
another format, the registry class or the row model. If it seems to, the row model is
missing a field: raise that as a schema change first.

## Steps

0. Create `__init__.py` (one-line docstring) in every new package directory you add under `src/` and `tests/`; the structural test rejects packages without one.
1. Pick the format code (`^[a-z][a-z0-9_]{1,31}$`, for example `csv`, `geojson`).
2. Write the exporter: deterministic column order, UTF-8, ISO 8601 UTC timestamps,
   `repr` for floats (lossless), SI units; the sidecar is
   `metadata.model_dump_json(indent=2)`.
3. Write the importer: check the header or schema first and raise on mismatch; validate
   every row with the row model; collect errors per row with its 1-based number; cap the
   number of rows; never write anything (the handler decides based on `dry_run`).
4. Register both in the composition root.
5. Write the tests below; the round-trip property test is mandatory whenever both
   directions exist.
6. Document the format in `docs/architecture/data-formats.md`.

## Templates

### `src/yakhnama/modules/exchange/infrastructure/formats/events_csv.py`
```python
"""CSV import and export of the events dataset (format code ``csv``).

Patterns: Strategy, Registry (registered by format code in the composition root).

One file per format. Adding a format never edits this file or the registry class.
"""

import csv
import io
from collections.abc import Sequence
from typing import Final

import pydantic

from yakhnama.modules.exchange.application.formats import (
    EventExchangeRow,
    ExportArtifact,
    ExportMetadata,
    ImportReport,
    RowResult,
)

COLUMNS: Final = (
    "event_id",
    "hazard_code",
    "title",
    "occurred_at",
    "date_precision",
    "longitude",
    "latitude",
)
CSV_MEDIA_TYPE: Final = "text/csv"
MAX_IMPORT_ROWS: Final = 100_000


class CsvEventExporter:
    """Write events as RFC 4180 CSV plus a JSON metadata sidecar.

    Implements: Strategy (``Exporter``).
    """

    @property
    def format_code(self) -> str:
        """Return the registry key of this format."""
        return "csv"

    def export(
        self,
        rows: Sequence[EventExchangeRow],
        metadata: ExportMetadata,
    ) -> ExportArtifact:
        """Serialise ``rows`` and their metadata.

        Args:
            rows: The rows to export, already filtered.
            metadata: Licence, generation time, filters, schema version, citation.

        Returns:
            CSV bytes (UTF-8, header row, CRLF line endings) and the JSON sidecar.
        """
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(COLUMNS)
        writer.writerows(
            (
                str(row.event_id),
                row.hazard_code,
                row.title,
                row.occurred_at.isoformat(),
                row.date_precision.value,
                repr(row.longitude),
                repr(row.latitude),
            )
            for row in rows
        )
        return ExportArtifact(
            content=buffer.getvalue().encode("utf-8"),
            media_type=CSV_MEDIA_TYPE,
            file_name="events.csv",
            sidecar=metadata.model_dump_json(indent=2).encode("utf-8"),
        )


class CsvEventImporter:
    """Parse events CSV into a row-level validation report; never writes data.

    Implements: Strategy (``Importer``).

    Persisting valid rows, dry runs and per-batch atomicity belong to the import
    handler that calls this class.
    """

    @property
    def format_code(self) -> str:
        """Return the registry key of this format."""
        return "csv"

    def parse(self, content: bytes) -> ImportReport:
        """Validate every row of ``content``.

        Args:
            content: UTF-8 CSV with the ``COLUMNS`` header.

        Returns:
            One result per data row, valid or not, in file order.

        Raises:
            ValueError: If the file is not UTF-8, the header is wrong or the file has
                more than ``MAX_IMPORT_ROWS`` rows.
        """
        reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""))
        if tuple(reader.fieldnames or ()) != COLUMNS:
            message = f"expected columns {COLUMNS}, got {reader.fieldnames}"
            raise ValueError(message)
        results: list[RowResult] = []
        for number, record in enumerate(reader, start=1):
            if number > MAX_IMPORT_ROWS:
                message = f"more than {MAX_IMPORT_ROWS} rows"
                raise ValueError(message)
            results.append(_validate_row(number, record))
        return ImportReport(format_code=self.format_code, rows=tuple(results))


def _validate_row(number: int, record: dict[str, str]) -> RowResult:
    """Turn one CSV record into a valid row or a list of readable errors."""
    try:
        row = EventExchangeRow.model_validate(record)
    except pydantic.ValidationError as error:
        errors = tuple(
            f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"
            for detail in error.errors()
        )
        return RowResult(row_number=number, row=None, errors=errors)
    return RowResult(row_number=number, row=row, errors=())
```

### Registration in `src/yakhnama/platform/container.py` (architect)
```python
"""Registration lines to add in the composition root (``platform/container.py``).

Patterns: Composition Root, Registry.
"""

from yakhnama.modules.exchange.application.formats import ExchangeFormatRegistry
from yakhnama.modules.exchange.infrastructure.formats.events_csv import (
    CsvEventExporter,
    CsvEventImporter,
)


def build_exchange_registry() -> ExchangeFormatRegistry:
    """Create the registry with every supported format.

    Returns:
        A registry that knows every exporter and importer.
    """
    registry = ExchangeFormatRegistry()
    registry.register_exporter(CsvEventExporter())
    registry.register_importer(CsvEventImporter())
    return registry
```

In the real container this is part of the existing builder; only the two `register_...`
lines and the import are new.

### `tests/unit/modules/exchange/infrastructure/formats/test_events_csv.py`
```python
"""Unit tests for the events CSV format."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from yakhnama.modules.events.public import DatePrecision
from yakhnama.modules.exchange.application.formats import (
    AppliedFilter,
    EventExchangeRow,
    ExchangeFormatRegistry,
    ExportMetadata,
)
from yakhnama.modules.exchange.infrastructure.formats.events_csv import (
    COLUMNS,
    CsvEventExporter,
    CsvEventImporter,
)

METADATA = ExportMetadata(
    licence="CC-BY-4.0",  # EXAMPLE ONLY: the data licence is a maintainer decision.
    generated_at=datetime(2025, 1, 1, tzinfo=UTC),
    filters=(AppliedFilter(name="hazard_code", value="glof"),),
    schema_version="1.0.0",
    citation="Yakhnama contributors (2025). Example export.",
)
TITLES = st.text(
    alphabet=st.characters(exclude_categories=("Cs", "Cc")),
    min_size=1,
    max_size=200,
).filter(lambda title: title == title.strip() and title != "")
ROWS = st.builds(
    EventExchangeRow,
    event_id=st.uuids(),
    hazard_code=st.from_regex(r"^[a-z][a-z0-9_]{1,63}$", fullmatch=True),
    title=TITLES,
    occurred_at=st.datetimes(timezones=st.just(UTC)),
    date_precision=st.sampled_from(DatePrecision),
    longitude=st.floats(min_value=-180.0, max_value=180.0),
    latitude=st.floats(min_value=-90.0, max_value=90.0),
)
HEADER = ",".join(COLUMNS)


@given(rows=st.lists(ROWS, max_size=20))
def test_events_csv_export_then_import_returns_equal_rows(
    rows: list[EventExchangeRow],
) -> None:
    artifact = CsvEventExporter().export(rows, METADATA)

    report = CsvEventImporter().parse(artifact.content)

    assert [result.row for result in report.rows] == rows


def test_events_csv_export_writes_metadata_sidecar() -> None:
    artifact = CsvEventExporter().export([], METADATA)

    sidecar = json.loads(artifact.sidecar)

    assert sidecar["licence"] == "CC-BY-4.0"
    assert sidecar["generated_at"] == "2025-01-01T00:00:00Z"
    assert sidecar["filters"] == [{"name": "hazard_code", "value": "glof"}]
    assert sidecar["schema_version"] == "1.0.0"
    assert sidecar["citation"].startswith("Yakhnama contributors")


def test_events_csv_import_with_invalid_row_reports_row_number_and_field() -> None:
    content = (
        f"{HEADER}\n"
        f"{UUID(int=1)},glof,Example,2025-07-01T06:00:00+00:00,hour,74.0,36.0\n"
        f"{UUID(int=2)},glof,Example,2025-07-01T06:00:00,hour,74.0,36.0\n"
    ).encode()

    report = CsvEventImporter().parse(content)

    assert [result.is_valid for result in report.rows] == [True, False]
    assert report.rows[1].row_number == 2
    assert report.rows[1].errors[0].startswith("occurred_at")


def test_events_csv_import_with_wrong_header_raises_value_error() -> None:
    content = b"id,title\n1,Example\n"

    with pytest.raises(ValueError, match="expected columns"):
        CsvEventImporter().parse(content)


def test_exchange_registry_register_same_code_twice_raises_value_error() -> None:
    registry = ExchangeFormatRegistry()
    registry.register_exporter(CsvEventExporter())

    with pytest.raises(ValueError, match="csv"):
        registry.register_exporter(CsvEventExporter())
```

## Required tests

- `test_<dataset>_<format>_export_then_import_returns_equal_rows` — hypothesis round trip
  over generated rows, including titles with commas, quotes and non-Latin scripts.
- `test_<dataset>_<format>_export_writes_metadata_sidecar` — licence, generation time,
  filters, schema version, citation.
- `test_<dataset>_<format>_import_with_invalid_row_reports_row_number_and_field`.
- `test_<dataset>_<format>_import_with_wrong_header_raises_value_error`.
- `test_exchange_registry_register_same_code_twice_raises_value_error` (once, in the
  base's tests).
- Dry run: in the import handler's tests (base), `test_import_rows_with_dry_run_commits_nothing`
  must pass with the new importer registered.

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/exchange -q
poetry run poe test-unit
poetry run poe cov
poetry run poe check
```

## Definition of Done

- [ ] One new file per format; no existing format, registry or row model edited.
- [ ] Exporter writes a metadata sidecar with licence, generation time, filters, schema
      version and citation.
- [ ] Importer validates the header and every row, reports errors per row, caps size,
      and never writes.
- [ ] Round-trip property test passes.
- [ ] Format documented.
- [ ] `standards-reviewer` approved; `security-reviewer` approved for importers (they
      parse untrusted input) and for any new dependency.
- [ ] Conventional Commit, for example `feat(exchange): add events csv format`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Lossy floats.** `f"{value:.4f}"` breaks the round trip and the record; use `repr`.
- **Timezones.** Export `isoformat()` of aware UTC datetimes; the importer rejects naive
  timestamps through the row model.
- **CSV injection.** Values starting with `=`, `+`, `-`, `@` open as formulas in
  spreadsheets. Decide the policy in an ADR before exporting free text to CSV for
  spreadsheet users.
- **Unbounded input.** Cap rows and bytes before parsing; a hostile file must fail fast.
- **Private data in exports.** Exports are public by design: reporter locations and
  casualty names never appear in them.
