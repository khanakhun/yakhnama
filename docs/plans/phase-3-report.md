# Phase 3 report — Core recording

## Summary

The backend now records hazard observations end to end. Citizens and organisations submit
immutable, revisioned `Report`s with private exact positions; triage runs as a background task
and only suggests. Media is uploaded through presigned URLs, hashed, sniffed, scanned through a
`MalwareScanner` port and published as an EXIF-stripped public copy only when clean, approved
and not sensitive. Moderators create canonical `Event`s from rounded report points, link reports,
relate events, and record append-only `ImpactClaim`s whose best figure is derived by a documented
policy. A `verification` state machine implements the specified transition table and only a
human can reach `verified`. Every domain event flows through the transactional outbox with
lease-based claiming, dead-lettering and retention, into an append-only `audit` log that stores
digests only. Public read APIs serve verified, published events with GeoJSON and filters. Taskiq
runs behind the `TaskQueue` port with a worker and scheduler. 4,688 tests run in the gate at
99.71 % branch coverage with 100 % on every domain and application layer.

## Delivered

| Task | Subagent | Files | Tests added | Status |
|------|----------|-------|-------------|--------|
| T1 Plan, dependencies, six module skeletons, contracts; kernel `SafeText`, `TaskQueue` port, coordinate rounding (ADR 0018) | lead + architect (Opus) | `pyproject.toml`, skeletons, `shared_kernel/{text,tasks,privacy}.py` | 100 % kernel | done |
| T2 Domains: provenance+audit, reports+media, events+verification, impacts claims + best figure | domain-modeler ×4 (Opus) | `modules/*/domain/`, 7 dictionaries, `docs/architecture/best-figure.md` | 243 + 325 + 365 + 407 | done |
| T3 Application layers (two halves) | application-engineer ×2 (Opus) | `modules/*/application/`, facades, fakes | 650 + 727 | done |
| T4 Persistence, migrations 0008–0014 | persistence-engineer ×2 (Opus) | `modules/*/infrastructure/`, `migrations/` | 252 + 125 integration | done |
| T5 Taskiq adapter, relay hardening (0007), scheduled tasks; S3/MinIO storage, EXIF, MIME, scanners | architect + integration-engineer (Opus) | `platform/tasks/`, `platform/outbox/`, `media/infrastructure/adapters/` | 725 unit + 47 integ; 274 + 17 | done |
| T6 API: 36 operations, snapshot, gate flow on fakes | api-engineer (Opus) | `modules/*/api/`, `tests/api/`, `tests/contract/` | 472 | done |
| Wiring: container, cross-module adapters, gate flow on PostGIS + MinIO | architect (Opus) | `platform/container.py`, `platform/wiring/` | 60 unit + 1 flow | done |
| T7 Docs | docs-writer (Sonnet) | `docs/architecture/recording.md`, dictionaries, Q77–Q173 | n/a | done |
| T8 Reviews, gate, report | security-reviewer (Opus), lead | | | see below |

## Quality gate

- `poetry run poe check`: **PASS**
  ```
  ruff format --check / ruff check      All checks passed
  mypy --strict                          Success: no issues found in 740 source files
  lint-imports                           Contracts: 8 kept, 0 broken
  pytest (all tiers, real PostGIS,
          MinIO and Redis)               4688 passed, coverage 99.71 % (branch on)
  cov-layers                             domain 100 %, application 100 %
  contract                               2 passed
  diff-cover vs main                     99 % on changed lines
  gitleaks git                           no leaks found
  pip-audit --strict                     No known vulnerabilities found
  ```
- Gate flow (`tests/integration/wiring/test_recording_flow_postgis.py`): upload a JPEG with GPS
  EXIF → complete → scan → approve → submit a report with it → triage → moderator creates the
  event, links, records a claim, publishes, verifies → anonymous `GET /events` shows the event
  with a best figure and a GeoJSON point rounded to 2 decimals; the public media copy has no
  EXIF; the audit log holds one digest-only entry per outbox event; no public payload carries the
  exact coordinates or accuracy.
- Reviews: security cycle 1 (mandatory) CHANGES REQUIRED with 6 items (2 high: presigned upload
  URL usable after completion to swap the file behind the recorded hash; MP4 and PDF public copies
  not metadata-stripped; 3 medium: decompression bombs in publication, anonymous source listing
  revealing private reports, four Phase 2 items silently deferred; 1 low: declared vs sniffed MIME)
  → all fixed (uploads sealed to a client-unreachable key with digest verification and quarantine on
  change; only JPEG/PNG/WebP publishable; header-based pixel and frame caps; citizen and
  organisation sources hidden unless cited by a public event or read by members/moderators;
  citations carry no report id; MIME mismatch fails the upload; deferred items recorded as
  Q174–Q177) → cycle 2 **APPROVE**. Standards review was not run as a
  separate cycle (cost); mechanical rules are enforced by the gate; nits logged as TD-30.

## ADRs added

0018 safe-text rules in the shared kernel (proposed).

## Deviations from the plan (and why)

1. `EventAttributes` stand-in was replaced by `HazardAttributesUnion` once the hazards facade
   exported the registry; the events domain imports the hazards and geography facades (Q-C2).
2. The NoOp scanner reports `unavailable`, not `clean`, so nothing is publishable in development
   without an explicit clean verdict (the gate test overrides it); this fails safe (Q-M9).
3. Reports mirror the hazard and place code formats instead of importing them (Q-R12).
4. Peak rainfall intensity is a velocity `Measurement`; the domain agent's physical argument was
   accepted.
5. Adding organisation members stays admin-only from Phase 2; claims and damage records have no
   read routes yet; `PATCH /moderation/events/{id}` applies one command per field.
6. Cross-module calls (source referencing, case opening) commit in their own transactions and
   are idempotent rather than atomic with the caller.
7. Delayed tasks are refused by the adapter; no module needs them yet.

## Open questions (blocking first)

Blocking before production or public media (not for Phase 4): ClamAV client unverified against
a real daemon (Q116); PDF and MP4 metadata not stripped from public copies (Q117). Everything
else is `docs/open-questions.md` Q77–Q173 with proposed defaults in use.

## Risks and technical debt (each with an issue reference)

| Ref | Item |
|-----|------|
| TD-30 | Standards nits unreviewed this phase: `claims_*` file naming, copied cursor parser and actor dependencies per module, `Adapter (port side)` labels. |
| TD-31 | Triage lost after a broker failure; periodic re-enqueue of untriaged reports needed. |
| TD-32 | Orphan sources and duplicate original objects have no clean-up job; `StoragePort` has no delete. |
| TD-33 | Outbox ids-only payload rule is enforced by size cap only. |
| TD-34 | `find_nearby` cannot use the GiST index (geography cast). |
| TD-35 | Merge does not move claims or the verification case. |
| TD-36 | Lead-owned skills still lag the real names (carried). |

## Proposed next phase (brief)

Phase 4, data exchange and ingestion framework: exporters (JSON, GeoJSON, CSV, GeoParquet) with
metadata sidecars and asynchronous export jobs; importers (CSV, GeoJSON) with dry run and
row-level validation reports; the `ingestion` module with dataset catalog, versions, runs,
the Template Method pipeline, `SourceAdapter` port, observation time series (Timescale-ready),
a STAC-aligned raster catalog, and one reference adapter reading a local temperature CSV
fixture; `docs/architecture/data-sources.md`; the analytics/ML readiness section.
