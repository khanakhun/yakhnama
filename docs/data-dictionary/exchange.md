# Data dictionary: exchange

The `exchange` module holds `ExportJob`s (one requested export of a dataset in one
format, with its stored file and metadata sidecar) and `ImportJob`s (one import of a
stored file, validated row by row, written in batches unless it is a dry run). It also
defines the historical backfill row contract, documented column by column in
[`docs/architecture/exchange.md`](../architecture/exchange.md#backfill-import-column-contract).
It was introduced in Phase 4. Column conventions are in [README.md](README.md).
**Proposed** marks a default the maintainer has not confirmed yet.

Source code: `src/yakhnama/modules/exchange/domain/`.

## Personal data

- Exports are public by design; which datasets a caller may export (reports are
  moderator-only, **proposed**) is an application policy.
- `error_summary`, issue messages and events never quote a file's cell values, which
  may carry personal data. Events carry ids, codes and counts only.
- Object keys are built by the application from job ids, never from uploaded file names.

## ExportJob (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Stable identity. | Platform `IdGenerator`. | Phase 4 |
| `requested_by` | `UUID` (v7) | — | The requesting user. | Authenticated actor. | Phase 4 |
| `dataset` | `ExportDataset` | — | `events`, `claims` or `reports`. | Requester. | Phase 4 |
| `format` | `ExportFormat` | — | `json`, `geojson`, `csv` or `geoparquet`. | Requester. | Phase 4 |
| `filters` | `ExportFilters` | — | Which rows are selected (below). | Requester. | Phase 4 |
| `status` | `JobStatus` | — | `queued` → `running` → `completed` / `failed`; a queued job may also fail or be `cancelled`. Final: `completed`, `failed`, `cancelled`. | Platform. | Phase 4 |
| `artifact` | `ArtifactRef`, nullable | — | The stored file; only when `completed`. Its media type is the format's. | Export worker. | Phase 4 |
| `sidecar` | `MetadataSidecar`, nullable | — | The file's metadata; only when `completed`; same dataset, format and filters as the job, checksum equal to the artifact's digest. | Export worker. | Phase 4 |
| `error_summary` | `str`, safe single-line text 1–1000, nullable | — | Why the job failed; only when `failed`. Never quotes row values. | Platform. | Phase 4 |
| `requested_at` | `datetime` (UTC) | UTC | When it was requested. | Platform `Clock`. | Phase 4 |
| `started_at` | `datetime` (UTC), nullable | UTC | When a worker started it. | Platform `Clock`. | Phase 4 |
| `finished_at` | `datetime` (UTC), nullable | UTC | When it completed, failed or was cancelled; `requested_at ≤ started_at ≤ finished_at`. | Platform `Clock`. | Phase 4 |
| `version` | `int`, 1–2³¹−1 | count | Optimistic-concurrency version, +1 per change. | Platform. | Phase 4 |

## ImportJob (aggregate root)

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `id` | `UUID` (v7) | — | Stable identity. | Platform `IdGenerator`. | Phase 4 |
| `requested_by` | `UUID` (v7) | — | The requesting moderator. | Authenticated actor. | Phase 4 |
| `format` | `ImportFormat` | — | `csv` or `geojson`. | Requester. | Phase 4 |
| `dry_run` | `bool` | — | `true`: validate and report only; nothing is written. | Requester (no default). | Phase 4 |
| `source_artifact` | `ArtifactRef` | — | The stored file to import. | Application, after upload. | Phase 4 |
| `status` | `JobStatus` | — | `queued` → `running` → `completed` / `failed`; a queued job may also fail. Never `cancelled`. | Platform. | Phase 4 |
| `report` | `ValidationReport`, nullable | — | Row-level validation report; required when `completed`, optional when `failed`. | Import worker. | Phase 4 |
| `writes.created_ids` | list of `UUID` (v7), ≤ 10 000, distinct | — | Ids of the events created, in order (claims are reached through their events). Empty for a dry run, and for a completed import whose report has blocking errors; at most one per valid row. | Import worker. | Phase 4 |
| `writes.lineage_source_id` | `UUID` (v7), nullable | — | The `provenance` source of type `dataset` every created record cites; required when anything was created. | Import worker. | Phase 4 |
| `writes.batches_applied` | `int`, 0–10 000 | count | Batches committed; at least 1 when anything was created. A failed import keeps what its committed batches wrote. | Import worker. | Phase 4 |
| `error_summary` | `str`, safe single-line text 1–1000, nullable | — | Why the import failed; only when `failed`. | Platform. | Phase 4 |
| `requested_at`, `started_at`, `finished_at` | `datetime` (UTC) | UTC | As for `ExportJob`. | Platform `Clock`. | Phase 4 |
| `version` | `int`, 1–2³¹−1 | count | Optimistic-concurrency version. | Platform. | Phase 4 |

## Value objects

### ExportFilters

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `bbox` | `BoundingBox`, nullable | degrees (WGS84) | Area the record's location must fall in. | Requester. | Phase 4 |
| `hazard_type` | hazard code, nullable | — | Hazard type. | Requester. | Phase 4 |
| `place_code` | place code, nullable | — | Gazetteer place. | Requester. | Phase 4 |
| `occurred_from` | `DateWithPrecision`, nullable | UTC + precision | Earliest moment of interest. | Requester. | Phase 4 |
| `occurred_to` | `DateWithPrecision`, nullable | UTC + precision | Latest moment of interest; never before `occurred_from`, compared with the same precision-aware rule as an event period. | Requester. | Phase 4 |
| `status` | code `^[a-z][a-z_]{1,31}$`, nullable | — | Record status to filter on; which statuses exist per dataset is checked by the application (**proposed**). | Requester. | Phase 4 |

`to_citation_fragment()` writes the set filters as `name=value` parts joined by `; ` in
the fixed order hazard type, place, bbox, from, to, status (or `no filters`). The bbox
uses exact float `repr`; dates are written only as precisely as known (`2022`,
`2022-06 season`, `2022-07`, `2022-07-15`, `2022-07-15T06Z`, full ISO for `exact`).

### ArtifactRef

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `object_key` | `str` `^[a-z0-9][a-z0-9/_.-]{3,255}$`, no `..` | — | Storage key. | Application. | Phase 4 |
| `byte_size` | `int`, 0–10 737 418 240 | byte | File size; maximum 10 GiB (**proposed**). | Storage adapter. | Phase 4 |
| `sha256` | `str`, 64 lower-case hex | — | Digest of the stored bytes. | Storage adapter. | Phase 4 |
| `media_type` | `str` `^[a-z]+/[a-z0-9][a-z0-9.+-]{0,99}$` | — | Media type without parameters. | Application. | Phase 4 |

### MetadataSidecar

Written next to every export as JSON by `to_json_bytes()` (the only serialisation
path; unset filters are left out).

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `licence.spdx_id` | SPDX id | — | Data licence, `CC-BY-4.0` per ADR 0010 (**proposed**). | Settings / ADR 0010. | Phase 4 |
| `licence.status` | `proposed` \| `accepted` | — | `proposed` until ADR 0010 is accepted. | Settings / ADR 0010. | Phase 4 |
| `licence.url` | `https` URL | — | Canonical licence text, `https://creativecommons.org/licenses/by/4.0/`. | ADR 0010. | Phase 4 |
| `generated_at` | `datetime` (UTC) | UTC | When the file was written. | Export worker `Clock`. | Phase 4 |
| `dataset` | `ExportDataset` | — | Dataset in the file. | Job. | Phase 4 |
| `format` | `ExportFormat` | — | Format of the file. | Job. | Phase 4 |
| `filters` | `ExportFilters` | — | Filters applied. | Job. | Phase 4 |
| `schema_version` | `MAJOR.MINOR` | — | Layout version; `1.0` (**proposed** start). | Code constant. | Phase 4 |
| `citation` | safe single-line text 1–2000 | — | How to cite the file; its wording is a maintainer decision. | Settings + filters fragment. | Phase 4 |
| `row_count` | `int`, 0–10⁹ | count | Data rows (records) in the file. | Export worker. | Phase 4 |
| `checksum` | 64 lower-case hex | — | SHA-256 of the file. | Export worker. | Phase 4 |
| `generator` | `yakhnama/<version>` | — | Software that wrote the file. | Package version. | Phase 4 |

### ValidationReport and RowIssue

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `issues` | list of `RowIssue`, ≤ 10 000 | — | Kept issues, ordered by row. | Importer + row contract. | Phase 4 |
| `rows_seen` | `int`, 0–10 000 | count | Data rows read (header not counted). | Importer. | Phase 4 |
| `rows_valid` | `int` | count | Rows without an `error`; `rows_valid + rows_rejected = rows_seen`. | Derived. | Phase 4 |
| `rows_rejected` | `int` | count | Rows with at least one `error`; counts every row even past the issue cap. | Derived. | Phase 4 |
| `is_truncated` | `bool` | — | Issues beyond the cap were dropped. | Derived. | Phase 4 |
| `RowIssue.row_number` | `int`, 1–10 000 | — | 1-based data row. | Importer. | Phase 4 |
| `RowIssue.field` | `str` `^[a-z][a-z0-9_.]{0,99}$`, nullable | — | Column at fault; `null` for a whole-row problem (for example no location, or an unknown column name that is not safe to echo). | Row contract. | Phase 4 |
| `RowIssue.message` | safe text 1–500 | — | What is wrong; never quotes the value. | Row contract. | Phase 4 |
| `RowIssue.severity` | `error` \| `warning` | — | `error` rejects the row. | Row contract. | Phase 4 |

### ImportBatch

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|
| `number` | `int` ≥ 1 | — | Position of the batch. | `plan_import_batches`. | Phase 4 |
| `first_row`, `last_row` | `int` ≥ 1, `first_row ≤ last_row` | — | Inclusive data-row range written in one transaction; default 500 rows (**proposed**). | `plan_import_batches`. | Phase 4 |

## Formats (`DEFAULT_FORMAT_REGISTRY`)

| code | direction | media type | extension | geometry |
|------|-----------|------------|-----------|----------|
| `json` | export | `application/json` | `.json` | no |
| `geojson` | export, import | `application/geo+json` (RFC 7946) | `.geojson` | yes |
| `csv` | export, import | `text/csv` (RFC 4180) | `.csv` | no |
| `geoparquet` | export | `application/vnd.apache.parquet` (**proposed**) | `.parquet` | yes |

## Backfill drafts

`ImportedEventDraft` (title, hazard type, `EventPeriod`, optional `EventGeometry`, up to
20 place codes, optional summary, `ImportedSourceDraft`, up to 5 `ImportedClaimDraft`s)
is what a valid row becomes; every column is listed in the
[contract](../architecture/exchange.md#backfill-import-column-contract). A claim draft's
`value` is an `impacts` `ClaimValue` (`count`, `measurement` in an SI unit, or
`monetary` with currency and price year); its confidence is required, as is
`claimed_at` with its precision.

## Persistence

`export_jobs` and `import_jobs` (migration `0017_exchange_jobs`) store every value
object beyond the top-level scalars as JSONB, validated through the aggregate on read
rather than mapped column by column, since a job's `filters`, `artifact`, `sidecar`,
`report` and `writes` are internal to this module and never queried by another table.

- **`export_jobs`.** `filters` (always present), `artifact` and `sidecar` (JSONB,
  present only once `completed`). Three indexes serve the three ways a job is listed
  or looked up: `ix_export_jobs_requested_by_requested_at`
  (`requested_by, requested_at, id`) for a user's own newest-first listing,
  `ix_export_jobs_requested_at_id` (`requested_at, id`) for a moderator's listing of
  every job, and `ix_export_jobs_status` for status lookups (for example a future
  sweep of stuck jobs, `docs/open-questions.md`).
- **`import_jobs`.** `source_artifact` (always), `report` (once produced) and `writes`
  (created event ids, the lineage source id, batches applied — empty for a dry run or
  a run that wrote nothing) as JSONB. Read by id only (`GET
  /moderation/imports/{job_id}`), so it carries no secondary index; there is no list
  route for import jobs yet (open question).

`requested_by` on both tables is a user id of the `identity` module and carries **no
foreign key** — the same cross-module rule every other Phase 3/4 table follows
(`docs/open-questions.md` Q162): the database cannot itself prevent a dangling
reference, and the application relies on users never being deleted instead.

## Proposed defaults (open)

- The backfill column contract, its five claim slots and 20 place codes per row.
- A row must have a geometry or a place code.
- Timestamps must carry a UTC offset and minutes, even for coarse precisions.
- 10 000 rows per import, 10 000 kept issues, 500 rows per batch, 10 GiB per artifact.
- `application/vnd.apache.parquet` for GeoParquet.
- The data licence (`CC-BY-4.0`, status `proposed`) until ADR 0010 is accepted.
