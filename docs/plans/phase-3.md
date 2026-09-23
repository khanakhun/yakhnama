# Phase 3 plan — Core recording

Status: **approved in advance** (maintainer's standing approval of 2026-09-23)
Branch: `phase/3-core-recording` (stacked on `phase/2-identity-api`)
Lead: Fable 5.1. Implementers and reviewers on Opus 5.5, `docs-writer` on Sonnet.

## 1. Goal

Record hazard observations end to end: citizens and organisations submit `Report`s (immutable,
revisioned, triaged), moderators create canonical `Event`s and link reports, impacts are recorded as
append-only `ImpactClaim`s with a documented best-figure policy, every fact carries a `Source`,
`MediaAsset`s are uploaded through presigned URLs with EXIF handling, SHA-256 deduplication and a
malware-scanner port, a `verification` state machine governs status changes, and an append-only
`audit` log is written by an outbox subscriber. Verified events are served through public read APIs
with GeoJSON and filters. Taskiq lands behind the `TaskQueue` port for the outbox relay and the
idempotency purge. No live external calls; MinIO via testcontainers in tests.

## 2. Inputs carried from Phase 2

Security reviewer's Phase 3 list: schedule `purge_expired`; shared safe-text type in the kernel
(surrogates, controls, bidi overrides) for every free-text field; round report coordinates to
`public_coordinate_decimals`, keep reporter GPS private, never create a Place from a reporter
location; JWKS streaming cap; token `typ` check; account-creation limits; IPv6 /64 rate keys;
multipart text validation; outbox payload contract (ids and non-personal fields only, size cap),
lease-based claiming, dead-letter and retention. TD-11/TD-26 skills lag; TD-20 nits.

## 3. Modules and their owners

| Module | Owns | Domain agent group |
|--------|------|--------------------|
| `provenance` | `Source` (types citizen, organisation, government, news, satellite, research, dataset), citation, URL, licence, retrieval time; immutable once referenced | A |
| `audit` | append-only `AuditEntry` (actor, action, target, before/after digest, timestamp, request id) written by an outbox subscriber; no update/delete path | A |
| `reports` | `Report` (reporter, `observed_at` + `DatePrecision`, point + GPS accuracy, description, original language, hazard type guess, client UUIDv7), revisions, triage results (Chain of Responsibility: EXIF plausibility → duplicate suspicion → PII scrub → spam; suggests only) | B |
| `media` | `MediaAsset` (object key, SHA-256, MIME, size, EXIF time and GPS, moderation status, sensitivity flag), private original and EXIF-stripped public copy, dedup by hash | B |
| `events` | `Event` (hazard type, started/ended with precision, point or polygon geometry, affected places, status, attributes JSONB validated by the hazards registry), `EventRelation` (`triggered_by`, `part_of`, `same_as`), report links; Factory from reports | C |
| `verification` | `VerificationCase` per target (report, event, claim) with the §6.3 transition table; reason required except `submitted`; only humans reach `verified` | C |
| `impacts` (extension) | `ImpactClaim` (append-only), `InfrastructureAsset` (bridge, road segment, water channel, power line, optional OSM id), `DamageRecord` (damaged, destroyed, washed away), best-figure aggregation policy | D |

## 4. Tasks

| # | Task | Owner | Depends on | Acceptance |
|---|------|-------|------------|------------|
| T1 | Dependencies (`aiobotocore` + `types-aiobotocore[s3]`, `pillow`, `filetype`, `taskiq`, `taskiq-redis`), module skeletons, import contracts, kernel `SafeText` type (surrogates, controls, bidi overrides), `TaskQueue` port in shared kernel, `public_coordinate_decimals` rounding helper | lead + architect | approval | `poe check` green |
| T2 | Domain groups A–D as in §3, each with data dictionary pages, factories, events, errors, hypothesis tests; the best-figure policy documented in `docs/architecture/best-figure.md` | domain-modeler ×4 | T1 | 100 % on domains |
| T3 | Application layers per group: commands (`SubmitReport` idempotent by client id, `ReviseReport`, `RunTriage`, `RequestUpload`, `CompleteUpload`, `CreateEventFromReports`, `LinkReportToEvent`, `RelateEvents`, `RecordImpactClaim`, `RegisterInfrastructureAsset`, `RecordDamage`, `TransitionVerification`, `RegisterSource`), queries (`ListEvents` with `bbox`/`hazard_type`/`place_id`/`status`/`from`/`to` Specifications, `GetEvent`, `GetEventImpacts` (best figures + claims), `GetEventTimeline`, `ListReports`, `GetReport`, `GetMediaAsset`), ports (`StoragePort`, `MalwareScanner`, `ExifReader`, `ImageTranscoder`, `TaskQueue` use), triage chain, audit subscriber, fakes | application-engineer ×2 | T2 | 100 % on application |
| T4 | Persistence: ORM, repositories, query services, migrations 0007–0012 (one per module, linear), PostGIS spatial queries (bbox, intersects), JSONB attributes validated by the hazards registry | persistence-engineer | T3 | `alembic check`; integration on PostGIS |
| T5 | Adapters: S3/MinIO storage (presigned PUT/GET, private and public buckets, SHA-256 verification on complete), EXIF reader and stripper via Pillow, magic-byte MIME check via `filetype`, `MalwareScanner` port with a `NoOpScanner` (dev) and a `ClamAV`-shaped adapter left unimplemented (documented), Taskiq adapter for `TaskQueue` with Redis broker, outbox relay task and idempotency purge task, lease-based claiming and dead-letter in the relay | integration-engineer + architect (relay) | T3 | MinIO and Redis testcontainer tests |
| T6 | API: `POST /reports` (Idempotency-Key + client id), `GET /reports`, `GET /reports/{id}`, `POST /reports/{id}/media` (presigned), `POST /media/{id}/complete`, `GET /events`, `GET /events/{id}`, `GET /events/{id}/impacts`, `GET /events/{id}/timeline`, moderation: `POST /moderation/events`, `POST /moderation/events/{id}/reports`, `POST /moderation/events/{id}/impact-claims`, `POST /moderation/verification/{case_id}/transitions`, `POST /moderation/sources`; public payloads round reporter coordinates; verified-only reads anonymous; snapshot regenerated | api-engineer | T3, T4 | API tests; contract |
| T7 | Docs, open questions, changelog | docs-writer | T6 | strict build |
| T8 | Reviews (security mandatory on reports, media, events API, storage, audit), gate, report | reviewers, lead | all | APPROVE |

Parallelism: T2 groups A–D in parallel; T3 in two halves (A+B, C+D) in parallel; T4 serial
(migrations); T5 parallel with T4; T6 after T4; T7 after T6.

## 5. Gate

`poe check` green; `poe up && poe migrate && poe seed`; a manual flow: submit a report with media
through presigned upload against MinIO, moderator creates an event, links the report, records a
claim, transitions verification to `verified`, and the event appears in `GET /events` with a
best figure and GeoJSON; security APPROVE.

## 6. Risks and open questions

| Item | Default |
|------|---------|
| Best-figure policy semantics (sum vs max vs latest, source ranking, overlapping claims) | per-metric `aggregation` from the registry; ties broken by source type rank (government > research > organisation > news > citizen) then latest; documented and marked proposed |
| Duplicate-report suspicion heuristics (distance, time window) | 2 km and 24 h, proposed |
| PII scrub in descriptions | regex for phone numbers, emails, CNIC; suggests redaction only |
| Reporter GPS privacy | stored exact; public payloads rounded to `public_coordinate_decimals` |
| EXIF plausibility | photo time within 7 days of observed_at and within 5 km of the report point, proposed |
| Sensitive imagery flag | manual moderator flag only; no ML |
| Malware scanning | port with NoOp in development; production adapter pending maintainer decision |
