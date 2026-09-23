# 0002. PostgreSQL with PostGIS as the system of record

- Date: 2026-09-23
- Status: accepted
- Deciders: lead agent, maintainer

## Context and problem statement

Every fact in Yakhnama has a place, a time and a source: reports with GPS points, events
with footprints, places in an administrative hierarchy with names in several languages and
scripts, impact claims per metric, and later observations from sensors and satellites.
Researchers will query this data spatially and temporally; moderators will search place
names with inconsistent spellings. The store must enforce integrity (foreign keys,
constraints, transactions) because a lost or inconsistent record damages the only record
the region has.

Which database should be the system of record, and how should spatial, text, metric and
time-series data be shaped in it?

## Decision drivers

- Correctness: ACID transactions, foreign keys and check constraints, including the
  transactional outbox ([0007](0007-transactional-outbox-for-domain-events.md)).
- Provenance and auditability: append-only claims and revisions need relational integrity
  to sources.
- Spatial queries: point-in-polygon for administrative places, bounding-box and distance
  search, GeoJSON output.
- Multilingual fuzzy search on place names (English, Urdu, Shina, Burushaski, Balti, Wakhi,
  Khowar and alternative spellings).
- Research consumers: standard SQL, standard CRS, easy export.
- Small team: one mature, well-documented database to operate, with the same engine in
  development, CI (testcontainers) and production.
- Future growth of time-series observations without an API change.

## Considered options

1. PostgreSQL 16+ with PostGIS, `pg_trgm` and `unaccent` (chosen)
2. MongoDB (or another document store) holding GeoJSON
3. SQLite with SpatiaLite
4. PostgreSQL plus a separate time-series database from the start

## Decision outcome

Chosen option: **PostgreSQL 16 or later with the PostGIS, `pg_trgm` and `unaccent`
extensions**, because it combines transactional integrity, first-class spatial types and
fuzzy text search in one engine the team can run everywhere.

- Geometries are stored in WGS84, EPSG:4326 (`srid=4326`). Projections for area or
  distance calculations happen in queries, not in storage.
- `pg_trgm` and `unaccent` back fuzzy search over place names. How they apply to
  non-Latin scripts is settled when the places module is built.
- Impact metrics and observations are stored **long and narrow** (one row per metric or
  observation, with value, unit, time and source), not as one wide column per metric. This
  matches the metric registry (`AGENTS.md` §7) and lets new metrics be added without a
  migration.
- The observations table is designed so that it can later become a TimescaleDB hypertable
  without an API change: the time column is part of its primary key and unique constraints,
  and no API contract depends on physical table layout. Adopting TimescaleDB is a separate,
  later ADR.
- Hazard-type-specific attributes are stored as JSONB, validated on write by the hazard
  Strategy + Registry (discriminated Pydantic unions), never accepted as free-form `dict`.
- Local development uses a PostGIS 16 image in `docker-compose.yml` (Phase 0 task T8);
  integration tests use real PostGIS through testcontainers from Phase 1.

### Consequences

- Good, because outbox rows, audit rows and domain rows commit in one transaction.
- Good, because spatial and text search run where the data is, with indexes (GiST for
  geometry, GIN trigram for names).
- Good, because EPSG:4326 and GeoJSON are what research and GIS tools expect by default.
- Good, because long-narrow metrics map one-to-one to sourced impact claims and to
  Sendai/DesInventar-style exports.
- Good, because the path to a time-series extension is kept open without committing to it.
- Bad, because long-narrow tables make "one row per event with every metric" reports
  require pivots or materialised views, which are extra work in the read models.
- Bad, because JSONB attributes move part of the schema out of the database: integrity of
  hazard attributes depends on the application registry, and SQL-only consumers see
  less-typed data unless the data dictionary documents it.
- Bad, because storing in 4326 means distance and area in metres need a cast to geography or
  a projection in every such query; mistakes here produce wrong numbers silently, so these
  queries need integration tests.
- Bad, because requiring PostGIS and extensions narrows managed-hosting choices to providers
  that offer them.

## Pros and cons of the options

### PostgreSQL with PostGIS, `pg_trgm`, `unaccent`

- Good, because it is mature, open source and widely hosted, with a large GIS community.
- Good, because relational integrity, spatial types, JSONB and text search are in one
  engine.
- Bad, because extensions must be installed and version-managed in every environment.

### MongoDB or another document store with GeoJSON

- Good, because nested documents fit hazard-specific attributes naturally.
- Bad, because relational integrity between reports, events, claims and sources, which is
  the core of provenance, would be enforced only in application code.
- Bad, because multi-document transactions and the outbox pattern are more constrained, and
  research consumers expect SQL.

### SQLite with SpatiaLite

- Good, because it is a single file with zero operations.
- Bad, because concurrent writes from the API and background workers are serialised by a
  single writer lock, and SpatiaLite behind async SQLAlchemy and GeoAlchemy2 is a far less
  travelled path than PostGIS.
- Bad, because development and production would differ from the multi-user server we need.

### PostgreSQL plus a separate time-series database now

- Good, because a dedicated store can handle high-rate sensor data efficiently.
- Bad, because no such data volume exists yet; it would add a second system to operate and
  break single-transaction guarantees for observations that reference sources and places.
- Bad, because the long-narrow design already leaves an upgrade path inside PostgreSQL.

## More information

- `AGENTS.md` §7 (Place, Impact metric, Measurement, Date precision).
- Which metrics, units and hazard attributes exist is a domain question tracked in
  `docs/open-questions.md`, not decided here.
- If TimescaleDB is adopted, the ADR must also check its licence terms for the features
  used and hosting availability.
