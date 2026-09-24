# Phase 4 plan — Data exchange and ingestion framework

Status: **approved in advance** (maintainer's standing approval of 2026-09-23)
Branch: `phase/4-exchange-ingestion` (stacked on `phase/3-core-recording`)
Lead: Fable 5.1. Implementers and reviewers on Opus 5.5, `docs-writer` on Sonnet.

## 1. Goal

Let researchers get data out and let curators get history and external datasets in, without
touching the recording modules' internals. `exchange`: exporters for JSON, GeoJSON, CSV and
GeoParquet with a metadata sidecar (licence, generation time, filters, schema version, citation),
run as asynchronous jobs; importers for CSV and GeoJSON with `dry_run`, a row-level validation
report, atomic batches and recorded lineage, used for the historical backfill of events and their
impact claims. `ingestion`: the dataset catalog (`Dataset` → `DatasetVersion` → `IngestionRun`),
the Template Method `IngestionPipeline` (fetch → parse → validate → normalise → deduplicate →
persist → record_lineage), the `SourceAdapter` port, the narrow observation time-series table
designed to become a TimescaleDB hypertable without API change, a STAC-aligned raster asset
catalog, and one reference adapter reading a local CSV fixture of temperature observations. No
live network calls. `docs/architecture/data-sources.md` documents the candidate future sources.
The phase report carries the "readiness for the analytics/ML repository" section.

## 2. Carried over

Security items deferred from Phase 2/3 and now due: JWKS streaming cap (Q174), token `typ`
check (Q175), IPv6 /64 rate keys (Q177), `storage_access_key_id` production guard, outbox
ids-only structural test, `SourceCitationChecker` adapter (Q178, needs an events read).
Account-creation limits (Q176) stay open for the maintainer. Skills lag (TD-36) is closed this
phase by regenerating the lead-owned skills from the real code.

## 3. Tasks

| # | Task | Owner | Depends on | Acceptance |
|---|------|-------|------------|------------|
| T1 | Plan, branch, `pyarrow` dependency, `exchange` and `ingestion` skeletons, contracts, `data/fixtures/` | lead | approval | `poe check` green |
| T2a | `exchange` domain: `ExportFormat`/`ImportFormat` codes, `ExportRequest` (dataset `events|reports|claims`, filters, format), `ExportJob` (queued → running → completed/failed, artifact key, sidecar), `MetadataSidecar` (licence from ADR 0010 proposal, generated_at, filters, schema_version, citation, row count, checksum), `ImportJob` (dry_run, batches, status), `ValidationReport` (row-level: row number, field, message, severity), `ImportRow`/`ImportedEventDraft` models for the backfill schema (event + claims per row, documented CSV/GeoJSON contract) | domain-modeler | T1 | 100 % |
| T2b | `ingestion` domain: `Dataset` (publisher, licence required, spatial/temporal coverage, update frequency, status), `DatasetVersion` (version label, checksum of inputs, retrieved_at), `IngestionRun` (started, finished, status, row counts, errors as a report, input checksum, lineage link), `Observation` (station or grid-cell reference, variable code, value + unit, observed_at, quality flag, dataset version), `StationRef`/`GridCellRef`, `RasterAsset` STAC-aligned (id, footprint geometry, datetime, platform, cloud cover, bands, asset href, dataset version), `VariableCode` registry (temperature etc., proposed), `IngestionReport`; never ingest without a licence (invariant) | domain-modeler | T1 | 100 % |
| T3a | `exchange` application: `Exporter`/`Importer` Protocols + `FormatRegistry` (Strategy + Registry; adding a format never touches others), `RequestExport` (any authenticated user for verified public data; moderators for reports) enqueuing `exchange.run_export`, `RunExport` (system task: reads through the events/impacts/reports facades with the same visibility rules as the API, streams rows to the exporter, writes artifact + sidecar to object storage via the media `StoragePort`? no: an `ArtifactStore` port), `GetExportJob`, `RequestImport` (moderator; file already uploaded to storage or inline body ≤ 5 MiB; dry_run) enqueuing `exchange.run_import`, `RunImport` (parse → validate every row → report; if not dry run and no blocking errors, create events and claims per batch through the events/impacts facades inside one transaction per batch; records lineage via a `Source` of type `dataset`), `GetImportJob` | application-engineer | T2a | 100 % |
| T3b | `ingestion` application: ports (`SourceAdapter.fetch(version) -> RawPayload`, `DatasetRepository`, `ObservationRepository.append_many`, `RasterAssetCatalog`, `IngestionRunRepository`), `IngestionPipeline` Template Method base with the seven hooks and a `ValidationCollector` (failures collected, never crash silently), `RunIngestion(actor: admin, dataset_id, version)` handler enqueuing `ingestion.run`, `RegisterDataset`, `RecordDatasetVersion`, queries `ListDatasets`, `GetDataset`, `ListRuns`, `QueryObservations(dataset, variable, from, to, station)`, `ListRasterAssets(bbox, datetime range)` | application-engineer | T2b | 100 % |
| T4 | Persistence: `export_jobs`, `import_jobs` (+ report JSONB), `datasets`, `dataset_versions`, `ingestion_runs`, `observations` (narrow: dataset_version_id, station_ref, grid_ref, variable_code, value, unit, observed_at, quality_flag; primary key `(observed_at, dataset_version_id, variable_code, station_ref)` with `observed_at` first so Timescale can hypertable it; no FKs that block partitioning), `raster_assets` (footprint geometry GiST); migrations 0015–0017; `events` read `is_source_cited_by_public_event` for Q178 | persistence-engineer | T3 | `alembic check` |
| T5a | Format adapters: `JsonExporter`, `GeoJsonExporter` (FeatureCollection), `CsvExporter`, `GeoParquetExporter` (pyarrow, WKB geometry column, GeoParquet 1.1 `geo` metadata), each streaming; `CsvImporter`, `GeoJsonImporter`; `S3ArtifactStore`; round-trip property tests (export → import of the same rows) | integration-engineer | T3a | round trips |
| T5b | Reference adapter: `LocalCsvTemperatureAdapter` reading `data/fixtures/ingestion/temperature_sample.csv` (synthetic, documented as fixture), `TemperatureCsvPipeline` subclass, dataset catalog seed entry with licence, fixture-based tests with lineage assertions; task handlers `exchange.run_export`, `exchange.run_import`, `ingestion.run` bound | integration-engineer + architect (binding) | T3b | lineage recorded |
| T6 | API: `POST /exports` (Idempotency-Key), `GET /exports/{id}` (status, presigned download when completed, sidecar inline), `POST /moderation/imports` (dry_run), `GET /moderation/imports/{id}` (report), `GET /datasets`, `GET /datasets/{id}`, `GET /datasets/{id}/runs`, `POST /admin/ingestion/runs`, `GET /observations` (filters, cursor), `GET /raster-assets` (bbox, datetime, GeoJSON); snapshot | api-engineer | T3, T4 | contract |
| T7 | Platform leftovers: JWKS streaming cap, token `typ`, IPv6 /64 keys, storage key guard, outbox ids-only structural test, citation checker wiring, `expected_version` on events and verification commands? (open) | architect | T1 | tests |
| T8 | Docs: `data-sources.md`, `exchange.md`, `ingestion.md`, dictionaries, open questions, changelog; regenerate the lead-owned skills from the real code (lead installs) | docs-writer + lead | T6 | strict build |
| T9 | Security review (exports of personal data: reports export moderator-only and rounded; import injection: CSV formulas, huge files, zip bombs n/a; GeoParquet metadata), gate, report with the readiness section | reviewer, lead | all | APPROVE |

Parallelism: T2a ∥ T2b ∥ T7; then T3a ∥ T3b; then T4 ∥ T5a; then T5b ∥ T6; T8; T9.

## 4. Gate

`poe check`; `poe up && poe migrate && poe seed`; flow: request a GeoJSON export of verified
events → worker runs it → download link and sidecar; run a dry-run CSV import of two historical
events with claims → report with one deliberate row error → real import creates one event and
its claim with a `dataset` source; run the temperature fixture ingestion → observations queryable,
run recorded with checksum and counts; security APPROVE.

## 5. Open questions

| Question | Default |
|----------|---------|
| Backfill CSV/GeoJSON column contract | documented in `docs/architecture/exchange.md`, proposed |
| Export licence text before ADR 0010 is accepted | sidecar carries `CC-BY-4.0 (proposed)` and a `licence_status: proposed` field |
| Who may export reports | moderators only; public exports contain only verified events, claims and rounded centroids |
| Variable codes and units for observations | `air_temperature` in kelvin (stored SI; CSV fixture in °C converted), proposed |
| Observation primary key and Timescale | `(observed_at, dataset_version_id, variable_code, station_ref)`, proposed |
