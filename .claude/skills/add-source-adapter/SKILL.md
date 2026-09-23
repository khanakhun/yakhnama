---
name: add-source-adapter
description: Add an external data source - a SourceAdapter implementation, an IngestionPipeline subclass filling the Template Method hooks, a dataset catalog entry with its licence, a fixture file, fixture-based tests with lineage assertions, and a docs/architecture/data-sources.md entry.
---

# add-source-adapter

## When to use

- Yakhnama must ingest records from a dataset, feed or file published by someone else
  (inventories, bulletins, archives).
- Not for user uploads (reports, media) and not for import/export formats of our own
  dataset (`add-exchange-format`).

## Preconditions

- **Licence first.** The publisher's licence is known and recorded (SPDX id, licence URL,
  citation text). Without it, stop: never ingest without a recorded licence.
- The ingestion base exists in `src/yakhnama/modules/ingestion/application/pipeline.py`
  (integration-engineer). This skill assumes the contract below; if the real base
  differs, the base wins and this skill must be corrected.

  | Name | Contract |
  |------|----------|
  | `SourceAdapter` | Protocol: `async fetch() -> SourcePayload` |
  | `SourcePayload` | `content: bytes`, `media_type`, `retrieved_at: AwareDatetime`, `sha256` |
  | `IngestionPipeline[RawT, RecordT]` | `__init__(adapter, dataset, uow_factory)`; final `run() -> IngestionReport` calls the hooks in order |
  | hooks | `fetch()` (default: `adapter.fetch()`), `parse(payload) -> Sequence[RawT]`, `validate(raw) -> list[str]`, `normalise(raw) -> RecordT`, `deduplicate(records) -> Sequence[RecordT]`, `async persist(records, uow) -> int`, `async record_lineage(payload, records, uow)` |
  | `IngestionUnitOfWork` | `staged_records.stage(dataset_code, record_key, record)`, `lineage.add(LineageRecord)` |
  | `DatasetEntry` | validated entry of `data/reference/datasets.yaml`; `licence` is required |
  | `IngestionReport` | `parsed`, `persisted`, `duplicates`, `problems: tuple[RowProblem, ...]` |

- Fakes exist in `tests/fakes/ingestion.py` (test-engineer): `FakeSourceAdapter`,
  `FakeIngestionUnitOfWorkFactory` (with `uow.staged_records.records` and
  `uow.lineage.records`).
- No live network calls, ever, in this run: the adapter is tested through
  `httpx.MockTransport` serving a fixture file.

## Owning subagent

`integration-engineer` (adapter, pipeline, dataset entry, fixture, tests).
`docs-writer` formats the `data-sources.md` entry. `security-reviewer` reviews every new
outbound integration.

## Files

The example source "example glacial lakes" is **synthetic**: its columns, URL, publisher
and licence are placeholders to be replaced from the real publisher's documentation.

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `data/reference/datasets.yaml` | modify | Dataset catalog entry with licence |
| `src/yakhnama/modules/ingestion/infrastructure/adapters/<source>.py` | create | `SourceAdapter` over `httpx` |
| `src/yakhnama/modules/ingestion/application/pipelines/<source>.py` | create | `IngestionPipeline` subclass |
| `tests/fixtures/ingestion/<source>.<ext>` | create | Small, synthetic or licence-compatible sample |
| `tests/unit/modules/ingestion/application/pipelines/test_<source>.py` | create | Pipeline tests with fakes |
| `tests/integration/modules/ingestion/infrastructure/adapters/test_<source>.py` | create | Adapter tests with `MockTransport` + fixture (mirrors `src/`) |
| `src/yakhnama/platform/container.py` | modify (architect) | Wire adapter, `retry` Decorator, pipeline |
| `src/yakhnama/platform/settings.py` | modify (architect) | Source URL setting |
| `docs/architecture/data-sources.md` | modify | Source entry |
| `__init__.py` in every new package directory under `src/` and `tests/` (`application/pipelines/`, `infrastructure/adapters/` and their test mirrors) | create if absent | One-line module docstring; required by the structural test and so that same-named test files in different tiers import cleanly |

## Steps

0. Create `__init__.py` (one-line docstring) in every new package directory you add under `src/` and `tests/`; the structural test rejects packages without one.
1. Record the dataset in `data/reference/datasets.yaml`: code, title, publisher,
   homepage, `licence` (SPDX), `licence_url`, `citation`. Copy the licence exactly as
   published.
2. Build the fixture: the smallest file that exercises a valid row, a duplicate and an
   invalid row, with the real header. Use synthetic values unless the licence allows
   redistribution; never commit personal data.
3. Write the adapter in `infrastructure/adapters/`: `httpx.AsyncClient` injected,
   explicit `httpx.Timeout`, `raise_for_status()`, a payload size cap, SHA-256 of the
   bytes, `retrieved_at` from the injected `Clock`. Retries come from the `retry`
   Decorator in the composition root. The URL comes from settings.
4. Write the pipeline in `application/pipelines/`:
   - a source-shaped `...Row` model (strings, source units, `row_number`);
   - a normalised record model in SI units and WGS84;
   - `parse` fails loudly on an unexpected header;
   - `validate` reuses `normalise` and turns `ValidationError`s into row messages;
   - `deduplicate` states in its docstring which record wins and why, taken from the
     publisher's documentation (the template's "last row wins" is EXAMPLE ONLY);
   - `persist` stages records (nothing ingested is trusted by default);
   - `record_lineage` links every staged record to dataset code, checksum and retrieval
     time.
5. Write the tests below, then the `data-sources.md` entry.
6. Ask the architect to wire the adapter and settings; report it as an open question if
   you do not own those files.

## Templates

### `data/reference/datasets.yaml` (example entry)
```yaml
# Dataset catalog. Every source adapter needs an entry here BEFORE its first run;
# the ingestion base refuses datasets without a recorded licence.
schema_version: 1
datasets:
  - code: example_glacial_lakes
    # EXAMPLE ONLY: a synthetic source used by the add-source-adapter skill.
    title: Example glacial lake inventory
    publisher: Example publisher
    homepage: https://example.org/glacial-lakes
    # Copy the licence exactly as the publisher states it (SPDX id + URL).
    licence: CC-BY-4.0
    licence_url: https://creativecommons.org/licenses/by/4.0/
    citation: "Example publisher (2025). Example glacial lake inventory."
```

### `src/yakhnama/modules/ingestion/infrastructure/adapters/example_glacial_lakes.py`
```python
"""HTTP adapter for the example glacial lake inventory (synthetic source).

Patterns: Adapter.
"""

import hashlib

import httpx

from yakhnama.modules.ingestion.application.pipeline import SourcePayload
from yakhnama.shared_kernel.clock import Clock

CSV_MEDIA_TYPE = "text/csv"
# Explicit timeouts: a slow upstream must fail the run, never hang a worker.
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
MAX_PAYLOAD_BYTES = 50 * 1024 * 1024


class ExampleGlacialLakesAdapter:
    """Fetch the example inventory CSV over HTTPS.

    Implements: Adapter (port ``SourceAdapter``).

    Retries are added by the ``retry`` Decorator in the composition root, not here.
    """

    def __init__(self, client: httpx.AsyncClient, url: str, clock: Clock) -> None:
        """Create the adapter.

        Args:
            client: Shared HTTP client; tests pass one with ``httpx.MockTransport``.
            url: HTTPS URL from settings, never hard-coded.
            clock: Source of the retrieval timestamp (UTC).
        """
        self._client = client
        self._url = url
        self._clock = clock

    async def fetch(self) -> SourcePayload:
        """Download the CSV and fingerprint it.

        Returns:
            The raw bytes with checksum and retrieval time.

        Raises:
            httpx.HTTPStatusError: If the source answers with an error status.
            ValueError: If the body is larger than ``MAX_PAYLOAD_BYTES``.
        """
        response = await self._client.get(self._url, timeout=TIMEOUT)
        response.raise_for_status()
        content = response.content
        if len(content) > MAX_PAYLOAD_BYTES:
            message = f"payload of {len(content)} bytes exceeds the limit"
            raise ValueError(message)
        return SourcePayload(
            content=content,
            media_type=response.headers.get("content-type", CSV_MEDIA_TYPE),
            retrieved_at=self._clock.now(),
            sha256=hashlib.sha256(content).hexdigest(),
        )
```

### `src/yakhnama/modules/ingestion/application/pipelines/example_glacial_lakes.py`
```python
"""Ingestion pipeline for the example glacial lake inventory (synthetic source).

Patterns: Template Method.

The source, its columns and its licence are EXAMPLE ONLY. Copy this file for a real
source and replace every source-specific detail from the publisher's documentation.
"""

import csv
import io
from collections.abc import Sequence
from typing import Annotated

import pydantic
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from yakhnama.modules.ingestion.application.pipeline import (
    IngestionPipeline,
    IngestionUnitOfWork,
    LineageRecord,
    SourcePayload,
)

EXPECTED_COLUMNS = ("lake_id", "name", "longitude", "latitude", "area_km2")
SQUARE_METRES_PER_SQUARE_KILOMETRE = 1_000_000.0

NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
_AREA_KM2: TypeAdapter[float] = TypeAdapter(NonNegativeFloat)


class ExampleGlacialLakeRow(BaseModel):
    """One CSV row exactly as the source publishes it (strings, source units).

    Implements: Anti-Corruption Layer (source-shaped model).

    Attributes:
        row_number: 1-based data row number, for the validation report.
        lake_id: Source identifier.
        name: Source name.
        longitude: Longitude text.
        latitude: Latitude text.
        area_km2: Area in square kilometres, as text.
    """

    model_config = ConfigDict(frozen=True)

    row_number: int
    lake_id: str
    name: str
    longitude: str
    latitude: str
    area_km2: str


class ExampleGlacialLake(BaseModel):
    """A validated, normalised lake record in Yakhnama units (SI, WGS84).

    Implements: Value Object.

    Attributes:
        source_lake_id: Identifier in the source, kept for lineage.
        name: Name as published by the source.
        longitude: WGS84 longitude.
        latitude: WGS84 latitude.
        area_square_metres: Area in m².
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_lake_id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    longitude: Annotated[float, Field(ge=-180.0, le=180.0, allow_inf_nan=False)]
    latitude: Annotated[float, Field(ge=-90.0, le=90.0, allow_inf_nan=False)]
    area_square_metres: NonNegativeFloat


def _field_path(location: tuple[int | str, ...]) -> str:
    """Render a Pydantic error location; the bare area adapter has none."""
    return ".".join(str(part) for part in location) or "area_km2"


class ExampleGlacialLakesPipeline(
    IngestionPipeline[ExampleGlacialLakeRow, ExampleGlacialLake],
):
    """Ingest the example glacial lake CSV into the staging area with lineage.

    Implements: Template Method.
    """

    def parse(self, payload: SourcePayload) -> Sequence[ExampleGlacialLakeRow]:
        """Split the CSV into source-shaped rows.

        Args:
            payload: Bytes fetched by the adapter.

        Returns:
            One row model per data row.

        Raises:
            ValueError: If the header does not match ``EXPECTED_COLUMNS``; a changed
                layout must fail loudly, never be guessed.
        """
        reader = csv.DictReader(io.StringIO(payload.content.decode("utf-8")))
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            message = f"unexpected columns: {reader.fieldnames}"
            raise ValueError(message)
        return [
            ExampleGlacialLakeRow(row_number=number, **row)
            for number, row in enumerate(reader, start=1)
        ]

    def validate(self, raw: ExampleGlacialLakeRow) -> list[str]:
        """Return every problem with ``raw``; an empty list means valid.

        Validation reuses ``normalise`` so the checks and the conversion can never
        drift apart.

        Args:
            raw: One source row.

        Returns:
            Human-readable problems for the row-level report.
        """
        try:
            self.normalise(raw)
        except pydantic.ValidationError as error:
            return [
                f"{_field_path(detail['loc'])}: {detail['msg']}"
                for detail in error.errors()
            ]
        return []

    def normalise(self, raw: ExampleGlacialLakeRow) -> ExampleGlacialLake:
        """Convert a row to SI units and our field names.

        Args:
            raw: One source row.

        Returns:
            The normalised record.

        Raises:
            pydantic.ValidationError: If a value is missing, malformed or out of
                range.
        """
        area_square_kilometres = _AREA_KM2.validate_python(raw.area_km2)
        return ExampleGlacialLake.model_validate(
            {
                "source_lake_id": raw.lake_id.strip(),
                "name": raw.name.strip(),
                "longitude": raw.longitude,
                "latitude": raw.latitude,
                "area_square_metres": area_square_kilometres
                * SQUARE_METRES_PER_SQUARE_KILOMETRE,
            },
        )

    def deduplicate(
        self,
        records: Sequence[ExampleGlacialLake],
    ) -> Sequence[ExampleGlacialLake]:
        """Keep the last record per source id (EXAMPLE ONLY rule).

        The example source appends corrected rows after the original ones, so the
        later row wins. A real source needs its own rule, taken from the publisher's
        documentation and stated here.

        Args:
            records: Normalised records in source order.

        Returns:
            Records with unique ``source_lake_id``, in first-seen order.
        """
        unique: dict[str, ExampleGlacialLake] = {}
        for record in records:
            # Assigning to an existing key keeps its position but replaces the value.
            unique[record.source_lake_id] = record
        return list(unique.values())

    async def persist(
        self,
        records: Sequence[ExampleGlacialLake],
        uow: IngestionUnitOfWork,
    ) -> int:
        """Stage every record for moderator review; nothing is trusted by default.

        Args:
            records: Deduplicated records.
            uow: The open ingestion unit of work.

        Returns:
            How many records were staged.
        """
        for record in records:
            await uow.staged_records.stage(
                self.dataset.code,
                record.source_lake_id,
                record,
            )
        return len(records)

    async def record_lineage(
        self,
        payload: SourcePayload,
        records: Sequence[ExampleGlacialLake],
        uow: IngestionUnitOfWork,
    ) -> None:
        """Link every staged record to the exact bytes it came from.

        Args:
            payload: The fetched payload (checksum and retrieval time).
            records: The staged records.
            uow: The open ingestion unit of work.
        """
        for record in records:
            await uow.lineage.add(
                LineageRecord(
                    dataset_code=self.dataset.code,
                    record_key=record.source_lake_id,
                    source_sha256=payload.sha256,
                    retrieved_at=payload.retrieved_at,
                ),
            )
```

### `tests/fixtures/ingestion/example_glacial_lakes.csv`
```csv
lake_id,name,longitude,latitude,area_km2
EX-0001,Synthetic Lake A,74.10,36.20,0.25
EX-0002,Synthetic Lake B,74.30,36.40,1.5
EX-0002,Synthetic Lake B (repeat),74.30,36.40,1.5
EX-0003,Synthetic Lake C,not-a-number,36.60,0.1
```

### `tests/unit/modules/ingestion/application/pipelines/test_example_glacial_lakes.py`
```python
"""Unit tests for the example glacial lakes pipeline, with fakes only."""

import hashlib
from datetime import UTC, datetime

import pytest

from tests.fakes.ingestion import FakeIngestionUnitOfWorkFactory, FakeSourceAdapter
from yakhnama.modules.ingestion.application.pipeline import SourcePayload
from yakhnama.modules.ingestion.application.pipelines.example_glacial_lakes import (
    ExampleGlacialLakesPipeline,
)
from yakhnama.modules.ingestion.domain.datasets import DatasetEntry

# Inline copy of the fixture: unit tests do no file I/O.
CSV = (
    b"lake_id,name,longitude,latitude,area_km2\n"
    b"EX-0001,Synthetic Lake A,74.10,36.20,0.25\n"
    b"EX-0002,Synthetic Lake B,74.30,36.40,1.5\n"
    b"EX-0002,Synthetic Lake B (repeat),74.30,36.40,1.5\n"
    b"EX-0003,Synthetic Lake C,not-a-number,36.60,0.1\n"
)
RETRIEVED_AT = datetime(2025, 1, 1, tzinfo=UTC)
DATASET = DatasetEntry.model_validate(
    {
        "code": "example_glacial_lakes",
        "title": "Example glacial lake inventory",
        "publisher": "Example publisher",
        "homepage": "https://example.org/glacial-lakes",
        "licence": "CC-BY-4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "citation": "Example publisher (2025). Example glacial lake inventory.",
    },
)


def make_pipeline(
    content: bytes,
) -> tuple[ExampleGlacialLakesPipeline, FakeIngestionUnitOfWorkFactory]:
    """Build the pipeline over an in-memory payload."""
    payload = SourcePayload(
        content=content,
        media_type="text/csv",
        retrieved_at=RETRIEVED_AT,
        sha256=hashlib.sha256(content).hexdigest(),
    )
    uow_factory = FakeIngestionUnitOfWorkFactory()
    pipeline = ExampleGlacialLakesPipeline(
        FakeSourceAdapter(payload),
        DATASET,
        uow_factory,
    )
    return pipeline, uow_factory


async def test_example_glacial_lakes_run_stages_valid_unique_records() -> None:
    pipeline, uow_factory = make_pipeline(CSV)

    report = await pipeline.run()

    staged = uow_factory.uow.staged_records.records
    assert sorted(key for _, key in staged) == ["EX-0001", "EX-0002"]
    assert report.persisted == 2
    assert report.duplicates == 1
    assert uow_factory.uow.committed is True


async def test_example_glacial_lakes_run_with_repeated_id_keeps_last_row() -> None:
    pipeline, uow_factory = make_pipeline(CSV)

    await pipeline.run()

    record = uow_factory.uow.staged_records.records["example_glacial_lakes", "EX-0002"]
    assert record.model_dump()["name"] == "Synthetic Lake B (repeat)"


async def test_example_glacial_lakes_run_converts_area_to_square_metres() -> None:
    pipeline, uow_factory = make_pipeline(CSV)

    await pipeline.run()

    record = uow_factory.uow.staged_records.records["example_glacial_lakes", "EX-0001"]
    assert record.model_dump()["area_square_metres"] == pytest.approx(250_000.0)


async def test_example_glacial_lakes_run_reports_invalid_row_with_number() -> None:
    pipeline, _ = make_pipeline(CSV)

    report = await pipeline.run()

    assert [problem.row_number for problem in report.problems] == [4]
    assert "longitude" in report.problems[0].message


async def test_example_glacial_lakes_run_records_lineage_for_every_staged_record() -> (
    None
):
    pipeline, uow_factory = make_pipeline(CSV)

    await pipeline.run()

    lineage = uow_factory.uow.lineage.records
    assert {record.record_key for record in lineage} == {"EX-0001", "EX-0002"}
    assert {record.source_sha256 for record in lineage} == {
        hashlib.sha256(CSV).hexdigest(),
    }
    assert {record.retrieved_at for record in lineage} == {RETRIEVED_AT}


async def test_example_glacial_lakes_run_with_changed_header_raises_value_error() -> (
    None
):
    pipeline, uow_factory = make_pipeline(b"id,name\nEX-1,Lake\n")

    with pytest.raises(ValueError, match="unexpected columns"):
        await pipeline.run()

    assert uow_factory.uow.committed is False
```

### `tests/integration/modules/ingestion/infrastructure/adapters/test_example_glacial_lakes.py`
```python
"""Adapter tests for the example glacial lakes source, served from a fixture file.

``httpx.MockTransport`` answers in-process, so no network is used.
"""

import hashlib
from pathlib import Path

import httpx
import pytest

from tests.fakes.clock import FixedClock
from yakhnama.modules.ingestion.infrastructure.adapters.example_glacial_lakes import (
    ExampleGlacialLakesAdapter,
)

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "ingestion" / "example_glacial_lakes.csv"
)
URL = "https://example.org/glacial-lakes.csv"


def serve(status_code: int, content: bytes) -> httpx.AsyncClient:
    """Return a client whose every request is answered in-process."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        return httpx.Response(
            status_code,
            content=content,
            headers={"content-type": "text/csv"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def fixture_bytes() -> bytes:
    """Read the fixture once, outside the event loop."""
    return FIXTURE.read_bytes()


async def test_example_glacial_lakes_adapter_fetch_returns_bytes_and_checksum(
    fixture_bytes: bytes,
) -> None:
    content = fixture_bytes
    clock = FixedClock()

    async with serve(200, content) as client:
        payload = await ExampleGlacialLakesAdapter(client, URL, clock).fetch()

    assert payload.content == content
    assert payload.sha256 == hashlib.sha256(content).hexdigest()
    assert payload.retrieved_at == clock.instant


async def test_example_glacial_lakes_adapter_on_server_error_raises_http_error() -> (
    None
):
    async with serve(503, b"") as client:
        adapter = ExampleGlacialLakesAdapter(client, URL, FixedClock())

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.fetch()
```

### `docs/architecture/data-sources.md` — entry

```markdown
## example_glacial_lakes — Example glacial lake inventory (EXAMPLE ONLY)

| Item | Value |
|------|-------|
| Publisher | Example publisher |
| Homepage | https://example.org/glacial-lakes |
| Licence | CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/) |
| Citation | Example publisher (2025). Example glacial lake inventory. |
| Access | HTTPS download of one CSV; URL from `YAKHNAMA_...` setting |
| Adapter | `yakhnama.modules.ingestion.infrastructure.adapters.example_glacial_lakes` |
| Pipeline | `yakhnama.modules.ingestion.application.pipelines.example_glacial_lakes` |
| Fixture | `tests/fixtures/ingestion/example_glacial_lakes.csv` (synthetic) |
| Units | source km² → stored m²; WGS84 degrees |
| Deduplication | EXAMPLE ONLY: last row per `lake_id` wins (the source appends corrections) |
| Lands in | ingestion staging area, pending moderator review |
| Refresh | manual; no schedule until the maintainer decides |
| Known issues | — |
```

## Required tests

- Pipeline (unit, fakes, inline payload):
  `test_example_glacial_lakes_run_stages_valid_unique_records`,
  `test_example_glacial_lakes_run_with_repeated_id_keeps_last_row`,
  `test_example_glacial_lakes_run_converts_area_to_square_metres`,
  `test_example_glacial_lakes_run_reports_invalid_row_with_number`,
  `test_example_glacial_lakes_run_records_lineage_for_every_staged_record`,
  `test_example_glacial_lakes_run_with_changed_header_raises_value_error`.
- Adapter (fixture + `MockTransport`):
  `test_example_glacial_lakes_adapter_fetch_returns_bytes_and_checksum`,
  `test_example_glacial_lakes_adapter_on_server_error_raises_http_error`.
- Catalog: the dataset entry validates (reference-data test for `datasets.yaml`, owned
  with the ingestion base).

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/ingestion tests/integration/modules/ingestion -q
poetry run poe test-unit
poetry run poe test-integration     # PostGIS/MinIO fixtures from Phase 1
poetry run poe security
poetry run poe check
```

## Definition of Done

- [ ] Dataset entry with SPDX licence, licence URL and citation exists before any run.
- [ ] Adapter uses injected `httpx.AsyncClient`, explicit timeouts, a size cap, SHA-256
      and the injected clock; no hard-coded URL.
- [ ] Pipeline implements every hook; records are staged, never trusted; lineage is
      recorded for every staged record.
- [ ] Fixture-based tests pass with no network access.
- [ ] `docs/architecture/data-sources.md` entry added.
- [ ] `standards-reviewer` and `security-reviewer` approved.
- [ ] Conventional Commit, for example `feat(ingestion): add example glacial lakes source`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Unknown licence.** "Publicly downloadable" is not a licence. No entry, no ingestion.
- **Silent schema drift.** A renamed column must fail `parse`, not produce empty values.
- **Units.** Convert to SI in `normalise`; name fields with their unit.
- **Coordinates.** Confirm the source CRS; reproject to EPSG:4326 in the adapter's
  Anti-Corruption Layer if it is not WGS84, and test it.
- **Blocking I/O in async tests.** Read fixture files in a sync fixture (Ruff `ASYNC240`).
- **Network in tests.** Only `httpx.MockTransport`; never a real URL, not even
  `localhost` services the test did not start.
- **Unmarked integration tests never run.** `poe test-integration` selects
  `-m integration`; every module under `tests/integration/` sets
  `pytestmark = pytest.mark.integration`.
- **SSRF.** Source URLs come from settings, never from user input.
