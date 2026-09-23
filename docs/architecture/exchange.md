# Exchange: exports, imports and the backfill contract

The `exchange` module gets data out of Yakhnama (exports to JSON, GeoJSON, CSV and
GeoParquet, each with a metadata sidecar) and gets historical records in (CSV and
GeoJSON imports with a dry run and a row-level validation report). Fields and units are
in the [data dictionary](../data-dictionary/exchange.md). Both directions run as
background jobs (`ExportJob`/`ImportJob`), started by `POST` under `/api/v1`, executed
by a Taskiq worker (`poetry run poe worker`) through `exchange.run_export` and
`exchange.run_import`, and read back through `GET`. Source code:
`src/yakhnama/modules/exchange/`.

## Exports

Any authenticated user may export the `events` or `claims` datasets; only moderators
may export `reports` (`export_policy`, **proposed**, `docs/open-questions.md`). Every
export is filtered through the same visibility rules the API itself applies: a
non-moderator only ever sees `published`/`verified` events, claims of events they may
see, and reports at their **rounded** public position, never the reporter's exact GPS
point (`ExportRowSource`, `_RowStream` in `application/handlers.py`, which rounds a
report's coordinates again as a safeguard even though the source should already have
done so).

**Formats and streaming.** Every exporter (`JsonExporter`, `GeoJsonExporter`,
`CsvExporter`, `GeoParquetExporter`) writes one dataset's rows to a `BinarySink` as an
async stream: no exporter holds the whole dataset in memory, and a `MeteredSink` wraps
the sink to compute the file's SHA-256 and size while it is written and to refuse
writing past `ARTIFACT_MAX_BYTES` (10 GiB, **proposed**). `GeoJsonExporter` writes a
`FeatureCollection`; a claim (which carries no geometry) becomes a `Feature` with
`geometry: null`, in every geometry-capable format (**proposed**, open question).

**Job lifecycle.** `queued → running → completed | failed`; a queued job may also be
`cancelled` by its owner or a moderator (`CancelExport`) before a worker starts it — a
job a worker has already started, or one already final, cannot be cancelled
(`JobStateError`). `RunExportHandler` is at-least-once safe: a repeated delivery of a
`running` or final job is a no-op (`RunExportHandler.__call__` returns `None`), so two
workers picking up the same message never write the file twice. Every run re-checks
`export_policy` against the actor rebuilt from the stored `requested_by` id
(`ActorLookup`), so a user demoted or suspended after requesting the export gets
nothing when it finally runs. A failure never copies the triggering exception's text
onto the job (it may quote row content or adapter internals); `export_failure_summary`
picks one fixed sentence by the error's family (permission, not-found, validation, the
size limit, or a generic failure/internal-error sentence).

**Where files go.** `exports/<job_id>/<dataset><extension>` for the data file and
`exports/<job_id>/<dataset>.sidecar.json` for the metadata sidecar (also embedded
inline in the job's `sidecar` field, so a client never has to fetch it separately).
`GET /exports/{id}` asks the `ArtifactStore` for a fresh presigned `download_url` at
read time, on every read, so the link is never stale even though the underlying object
never moves — there is no dedicated lifecycle rule yet for expiring or sweeping
completed export files themselves (open question).

**Metadata sidecar.** Every completed export's sidecar carries: the licence
(`licence.spdx_id` `CC-BY-4.0`, `licence.status: proposed` until ADR 0010 is accepted,
`licence.url`), `generated_at` (exact, the worker's clock), the `dataset` and `format`,
the `filters` actually applied, a `schema_version` (`1.0`, **proposed** layout version
for the flat column layout below), a `citation` line, `row_count`, the file's SHA-256
`checksum`, and `generator` (`yakhnama/<package version>`). The citation
(`export_citation` in `application/handlers.py`) reads:

```text
Yakhnama contributors (<year>). Yakhnama <dataset> export <job id>
(<filters as name=value; ...>), generated <moment>. Licence: <licence label>.
```

"Yakhnama contributors" is a fixed, **proposed** author name (`CITATION_AUTHOR`) pending
the maintainer's ADR 0010 decision on the dataset's real citation wording.

## Imports

Importing is moderator-only work (`import_policy`), in four steps:

1. **Upload grant** — `POST /moderation/imports/uploads` returns a presigned `PUT` to
   a key the server chooses, `imports/<upload_id>/source<extension>`, capped at
   `IMPORT_UPLOAD_MAX_BYTES` (50 MiB, **proposed**); nothing is stored until the
   client uploads. The presigned `PUT` cannot itself enforce that cap end to end — see
   "Known limits" below.
2. **Request** — `POST /moderation/imports` names exactly that key (or, for a small
   file, an inline body up to the same size sent as `inline_csv`; the domain accepts
   exactly one of the two), the file's measured size and SHA-256, its format, and an
   explicit `dry_run` (there is no default: a client always says which it means). The
   handler stores the job `queued` and enqueues `exchange.run_import`.
3. **Validation** — the worker streams the file once through a `MeteredSource`
   (`open_source`), checks the header against the backfill column contract
   (`require_backfill_header`), tokenises every row
   (`ImportedEventDraft.from_flat_row`), checks the shape-valid drafts' codes against
   the hazards, impacts and geography registries (`BackfillReferenceChecker`), and
   verifies the whole file against its **declared** size and digest only after reading
   every byte (`MeteredSource.verify`) — a file that changed after the request was
   made is never imported. The result is one `ValidationReport`: `rows_seen`,
   `rows_valid`, `rows_rejected`, and up to 10 000 kept `RowIssue`s (`row_number`,
   `field` or `null` for a whole-row problem, a `message` that never quotes a cell,
   and `severity`).
4. **Write, unless blocked** — **a dry run, an empty file, or a report with any
   blocking error (any `error`-severity issue) writes nothing at all**
   (`docs/plans/phase-4.md` §1, `docs/open-questions.md`): the job completes with only
   its report. Otherwise, exactly one `provenance` source of type `dataset` is
   registered for the whole import (`LineageSourceRegistrar`, citing the import job id,
   the format and the backfill schema version), and every valid row is written in
   batches (`plan_import_batches`, `DEFAULT_IMPORT_BATCH_SIZE` = 500 rows,
   **proposed**) through `HistoricalEventWriter.batch()`.

**Batch atomicity.** One `BatchScope` is one database transaction: everything written
through it (the row's own citizen/organisation-equivalent `dataset` source reuse, the
event, its claims, and their verification cases) commits or rolls back together
(`application/ports.py`, `HistoricalEventWriter`). A batch that fails **fails the whole
job**, but the job keeps the ids and the `batches_applied` count of the batches
committed before it (`ImportWrites.created_ids`, `.batches_applied`) — the import is
therefore atomic **per batch**, not atomic across the whole file; a large import that
fails partway through leaves the earlier batches' events and claims recorded.

**Lineage.** Every event and claim an import writes cites two sources: its own row's
`ImportedSourceDraft` (the historical citation given in the row itself) and the one
`dataset` source registered for the whole import job (the provenance of the *file*, as
opposed to the provenance of the *fact*). Both are `Source` records reachable the same
way any other source is.

## The export column layout (CSV and GeoParquet)

CSV and GeoParquet do not share the backfill's row shape; they share a separate, flat,
typed **export** column layout (`infrastructure/adapters/flat_layout.py`,
`DatasetLayout`/`FlatColumn`), one per dataset (`events`, `claims`, `reports`), so a new
field is caught by one test (every export-row field must map to a column) instead of
drifting between the two formats:

- A `DateWithPrecision` becomes two columns: `<field>` (the UTC timestamp) and
  `<field>_precision`.
- `Coordinates` become `<prefix>longitude` and `<prefix>latitude`.
- A tuple of codes or ids (place codes, source ids) becomes one list column: CSV joins
  it with `;` (the same separator the backfill contract's `place_codes` uses),
  GeoParquet writes `list<string>`.
- A claim's polymorphic value becomes typed columns beside `value_kind`: `count`,
  `measurement_value` and `measurement_unit`, or `amount` (decimal text, so money
  stays exact), `currency` and `price_year` — whichever variant applies; the other
  variants' columns are empty/null.
- A claim's scope becomes `scope_place_code` and `scope_asset_id`.
- The geometry (an event's own `geometry`, else its `centroid` as a point, else none; a
  report's already-rounded point; **none for a claim**, which carries no location) is
  never a flat column: each format writes it natively — CSV as a trailing `geometry`
  column holding compact GeoJSON text, GeoParquet as a binary WKB `geometry` column.

## CSV export

RFC 4180, UTF-8, no byte-order mark, CRLF record terminators, a cell quoted only when
it holds a comma, a quote or a line break. Floats use `repr` (lossless); a monetary
amount is decimal text; an absent value is an empty cell; a spatial dataset's header
gets a trailing `geometry` column.

**Formula guard (spreadsheet injection).** Free text in an export ultimately comes from
a contributor, and a spreadsheet that opens a CSV treats a cell starting with `=`, `+`,
`-`, `@`, a tab or a carriage return as a formula. Every **text** cell that starts with
one of those characters is written with a leading apostrophe (`'=1+1`), the OWASP
recommendation: the spreadsheet shows the literal text instead of evaluating it, and a
reader of the raw file strips at most one leading apostrophe from such a cell. Numeric,
timestamp and GeoJSON cells are never prefixed — they come from typed values, so a
negative longitude is a number, never a formula, and prefixing them would corrupt them.
This rule does not yet have its own ADR (open question).

## GeoParquet export

Parquet with GeoParquet 1.1.0 metadata (`geo` file key-value metadata: `version`,
`primary_column: "geometry"`, `columns.geometry.encoding: "WKB"`, the sorted
`geometry_types` actually written, and a `bbox` over every geometry — all left out
when there is none). `crs` is deliberately **absent**: the specification defines an
absent `crs` as OGC:CRS84 (WGS84, longitude first), exactly Yakhnama's CRS, whereas
`"crs": null` would declare the CRS *unknown*. Rows are written in record batches of
`BATCH_ROWS` (1000), each one Parquet row group, so the dataset is never held whole in
memory; a claim's `geometry` column is entirely null, since claims carry no location
(**proposed**, open question). `store_schema=False`: readers take the schema, `geo`
metadata included, from the file's own metadata rather than an embedded Arrow schema.

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

## Route table

Auth column as in [`api.md`](api.md#route-table): **auth** = any authenticated user;
**auth (policy)** = authenticated and further checked by an exchange or identity
policy; `M` marks a route under `/api/v1/moderation`. Every creating `POST` accepts
`Idempotency-Key` and answers `202 Accepted` (the job is stored and queued; a worker
runs it).

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/api/v1/exports` | auth (policy) | `export_policy(dataset)`: any authenticated user for `events`/`claims`, moderators only for `reports`; `202`, body is the queued job; `Location`, `ETag`. |
| `GET` | `/api/v1/exports` | auth | Every job for a moderator, else the caller's own; cursor pagination. |
| `GET` | `/api/v1/exports/{job_id}` | auth (policy) | Owner or moderator, else `404` (job ids are not probable); `ETag`; a fresh `download_url` and the inline sidecar once `completed`. |
| `DELETE` | `/api/v1/exports/{job_id}` | auth (policy) | Owner or moderator; `204`; `409` once a worker has started it or it is final. |
| `POST` | `/api/v1/moderation/imports/uploads` | auth (policy, M) | `import_policy`; grants a presigned `PUT` under `imports/<upload_id>/source<extension>`, capped at `IMPORT_UPLOAD_MAX_BYTES`; `201`. |
| `POST` | `/api/v1/moderation/imports` | auth (policy, M) | `import_policy`; names the uploaded key (or an inline body), size, digest, format and `dry_run`; `202`; `Location`, `ETag`. |
| `GET` | `/api/v1/moderation/imports/{job_id}` | auth (policy, M) | `import_policy`; `ETag`; the row-level report and what was written, once produced. |

`GET /api/v1/moderation/imports` (listing every import job, as `GET /exports` lists
export jobs) does not exist yet (open question).

## Further reading

- `docs/data-dictionary/exchange.md` — every field, unit and the persistence columns of
  `export_jobs` and `import_jobs`.
- `docs/architecture/ingestion.md` — the companion module for data coming *in* from
  external publishers, as opposed to historical backfill or export.
- `docs/architecture/api.md` — the `/api/v1` conventions this module follows
  unchanged (Problem Details, pagination, `Idempotency-Key`, `ETag`/`If-Match`, rate
  limiting).
- `docs/adr/0010-licensing.md` — the licence the sidecar's `licence.status: proposed`
  depends on.
- `docs/open-questions.md` — the Phase 4 open questions this page and the data
  dictionary reference.
