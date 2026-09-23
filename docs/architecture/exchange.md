# Exchange: exports, imports and the backfill contract

The `exchange` module gets data out of Yakhnama (exports to JSON, GeoJSON, CSV and
GeoParquet, each with a metadata sidecar) and gets historical records in (CSV and
GeoJSON imports with a dry run and a row-level validation report). Fields and units are
in the [data dictionary](../data-dictionary/exchange.md). The rest of this page (job flow,
storage, permissions) is written when the application and API layers exist.

## Backfill import column contract

**Status: proposed** (Phase 4 plan §5), schema version `1.0`
(`BACKFILL_SCHEMA_VERSION`). Source of truth: `src/yakhnama/modules/exchange/domain/backfill.py`.

One row is one historical event, the source it cites, and up to five impact claims.
Importers only tokenise: a CSV importer passes each record as `column → text` to
`ImportedEventDraft.from_flat_row(row_number, cells)`; a GeoJSON importer passes each
feature's `properties` (values as text) and its `geometry` serialised as JSON under the
`geometry` key. The call returns a valid draft, or every issue of that row (row number,
column, message, severity). It never raises for bad cell content.

### Rules for every cell

- Surrounding whitespace is ignored; an empty cell is an absent value.
- A header must contain the required columns below, only contract columns, and each at
  most once (`require_backfill_header`, raising `ImportContractError`). Optional columns
  may be left out entirely.
- Timestamps are ISO 8601 with minutes and a UTC offset: `2022-07-15T06:00Z`,
  `2022-07-15T06:00:00+05:00`, fractions up to microseconds. Dates alone and bare
  numbers are refused (a bare number would otherwise be read as a Unix time). Every
  timestamp is stored in UTC with its precision.
- Precisions: `exact`, `hour`, `day`, `month`, `season`, `year`. A timestamp and its
  precision are given together or not at all.
- Decimals are plain (`12`, `-0.5`, `1.5e3`); `nan`, `inf`, underscores and hex are
  refused. Whole numbers (`count` values, price years) are digits only.
- Free text follows the shared safe-text rules (NFC; no control, surrogate or
  bidirectional override characters; line breaks only where the table says so).
- Cells are at most 10 000 characters, numbers at most 64, `geometry` at most 1 000 000.
- Messages never quote a cell, because cells may hold personal data.

### Event and source columns

| column | required | format | meaning |
|--------|----------|--------|---------|
| `title` | yes | safe text, 3–200, one line | Short name of the event. |
| `hazard_type` | yes | hazard code `^[a-z][a-z0-9_]{1,63}$` | Hazard type; existence is checked by the application against the hazards registry. |
| `started_at` | yes | timestamp | When the event started. |
| `started_at_precision` | yes | precision | How precisely `started_at` is known. |
| `ended_at` | no | timestamp | When it ended; never before `started_at` read at its precision. |
| `ended_at_precision` | with `ended_at` | precision | How precisely `ended_at` is known. |
| `longitude` | with `latitude` | decimal, −180 to 180 | WGS84 point, degrees east. |
| `latitude` | with `longitude` | decimal, −90 to 90 | WGS84 point, degrees north. |
| `geometry` | no | GeoJSON `Point`, `Polygon` or `MultiPolygon` object, 2D, WGS84 | Mapped footprint; not together with `longitude`/`latitude`. |
| `place_codes` | no | up to 20 distinct place codes separated by `;` | Gazetteer places the event concerns (become `impacted` places, proposed). |
| `summary` | no | safe text, 1–2000, line breaks allowed | Curator's summary. |
| `source_citation` | yes | safe text, 1–1000, one line | How to cite the row's source. |
| `source_url` | no | `http`/`https` URL, printable ASCII, host required, no credentials, ≤ 2048 | Where the source is online. |
| `source_licence` | no | SPDX id `^[A-Za-z0-9.+-]{2,64}$` | Reuse terms of the source. |
| `source_licence_text` | no | safe text, 1–500 | Custom terms; not together with `source_licence`. |

A row must be located: `longitude` and `latitude`, a `geometry`, or at least one place
code (proposed).

### Claim columns

Five claim slots, `n` = 1 to 5, named `claim_<n>_<field>`. A slot with every cell empty
is skipped; otherwise its required cells must be present.

| field | required | format | meaning |
|-------|----------|--------|---------|
| `metric_code` | yes | metric code `^[a-z][a-z0-9_]{1,63}$` | Impact metric; kind, unit and currency are checked by the application against the metric registry. |
| `value_kind` | yes | `count`, `measurement` or `monetary` | What kind of number `value` is. |
| `value` | yes | `count`: whole number ≥ 0; `measurement`: decimal ≥ 0; `monetary`: decimal ≥ 0 with at most 2 decimal places | The figure. |
| `unit` | `measurement` only | SI unit name (`metre`, `square_metre`, `cubic_metre`, ...), never `count` | Unit of a measurement; for `count` empty or `count`; for `monetary` empty. |
| `currency` | `monetary` only | ISO 4217 code `^[A-Z]{3}$` | Currency of a monetary figure. |
| `price_year` | `monetary` only | year 1900–2100 | Year whose prices the amount is in; no conversion is applied. |
| `confidence` | yes | `low`, `medium`, `high` | How far the source's figure can be trusted. |
| `claimed_at` | yes | timestamp | When the source stated the figure. |
| `claimed_at_precision` | yes | precision | How precisely `claimed_at` is known. |
| `note` | no | safe text, 1–1000, line breaks allowed | Curator's note. |

### Column order

`BACKFILL_COLUMNS` is the event columns in the order above, then `claim_1_metric_code`
through `claim_1_note`, then slots 2 to 5. `ImportedEventDraft.to_flat_row()` writes every
column in that order (empty strings for absent values, a point as `longitude`/`latitude`,
other geometries as GeoJSON, floats with `repr`), and `from_flat_row` of that row returns
an equal draft.

### Limits

At most 10 000 data rows per import (`IMPORT_MAX_ROWS`, proposed) and 10 000 issues
kept per report (`REPORT_MAX_ISSUES`); rows beyond the issue cap are still counted. A
real import writes rows in batches of 500 (`DEFAULT_IMPORT_BATCH_SIZE`, proposed), one
transaction per batch, and never runs when the report has a blocking error.
