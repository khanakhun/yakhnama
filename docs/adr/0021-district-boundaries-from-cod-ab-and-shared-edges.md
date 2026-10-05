# 0021. District boundaries from OCHA COD-AB, published only as shared edges

- Date: 2026-10-05
- Status: proposed
- Deciders: lead agent, maintainer
- Amended: 2026-10-05 after review: the load checks the districts' coverage and
  refuses to publish an invalid one unless `--allow-invalid-coverage` is passed; the
  outline is closed with mitre joins so the clearance holds at sharp notches; the
  loader and the edge computation run in worker threads; a `304` is answered from the
  snapshot id without loading edges; the route's `ETag` and `Cache-Control` headers are
  documented in OpenAPI.

## Context and problem statement

The portal's map should show where Gilgit-Baltistan's districts meet. The maintainer
decided on 2026-10-05 that the boundary source is OCHA COD-AB for Pakistan via HDX
(open question Q1) and that the map draws **district lines only**: no international
boundary and no Line of Control may be drawn. The outer edge of the set of
Gilgit-Baltistan districts traces exactly those lines, and the boundary with
Khyber Pakhtunkhwa, so it must never leave the API. The gazetteer has 10 fixture
districts without geometry; COD-AB has 14 districts in the region. The geography module
had no boundary port (Q42).

How is the dataset obtained and trusted, where do its polygons live, how are the shared
edges computed and stored, and what does the public route return?

## Decision drivers

- The editorial rule is absolute: no line on the region's outline, whatever the data.
- Reproducibility: the same archive gives the same lines; a changed archive is noticed.
- No invented domain facts: district links are explicit and reviewed, mismatches reported.
- Licence: COD-AB is published under CC BY-IGO, and its attribution must travel with it.
- Low-end phones on slow links: a small payload (target under 150 KB), cached for long.
- Hexagonal layers: Shapely, httpx and zip files stay in infrastructure.
- The editorial rule must be testable.

## Considered options

Storage of the polygons:

1. The existing `Place.geometry` of each linked gazetteer district (chosen)
2. A dedicated boundary table keyed by the dataset's district code

Shared edges:

1. Computed at load time with Shapely and stored as an immutable snapshot (chosen)
2. Computed on request in PostGIS (`ST_Intersection`, `ST_LineMerge`,
   `ST_SimplifyPreserveTopology`) and cached in process
3. Using COD-AB's own `pak_adminlines.geojson` and filtering line types

## Decision outcome

**Polygons in `Place.geometry`; edges computed at load time with Shapely, stored as a
snapshot and served as GeoJSON lines.**

- **Pinned source.** `data/boundaries/cod_ab_pak_gb_districts.yaml`
  (`DistrictBoundarySource`) pins the archive by URL and SHA-256, names the member
  (`pak_admin2.geojson`) and region (`PK3` = `pk.gb`), carries the attribution and
  one link row per district: the COD-AB P-code, its name, and the gazetteer code or
  `null`. Links are written by hand; nothing is matched by name.
- **Loader.** `CodAbBoundaryLoader` (port `BoundaryLoader`) downloads the archive once
  into `Settings.boundary_cache_dir` (git-ignored, named by the digest), checks the
  SHA-256 after every download and before every read, refuses redirects away from
  https, caps the archive and member sizes, and reads only the region's features.
  `retrieved_at` is the cached file's modification time. The download runs only from
  `poetry run poe load-boundaries`; tests use `httpx.MockTransport` and a synthetic
  fixture. File operations, hashing and parsing (JSON, Shapely, Pydantic) run in worker
  threads (`asyncio.to_thread`), as does the edge computation, so a load never blocks
  the event loop of the process running it.
- **Matching.** `match_district_boundaries` reports, never resolves: districts the
  table leaves unlinked, districts the table lacks, rows whose district the file lacks,
  linked places the gazetteer lacks or has retired, gazetteer districts of the region
  (by ancestry, not by code prefix) with no boundary, and name differences.
- **Footprints.** Each linked active place gets the full COD-AB polygon through
  `Place.set_geometry` (an ordinary, idempotent aggregate change with its event).
  Footprints are never part of a public read model.
- **Centroids.** The loader also computes each district's representative point
  (Shapely `representative_point`, GEOS `PointOnSurface`: always inside the polygon,
  unlike a plain centroid of a concave district). A linked active place gets it as
  its `centroid` when it has none, or when its centroid is still the one the previous
  load set; a centroid from any other source is kept and reported
  (`boundary_centroid_kept`). The centroids a load set are recorded with its snapshot
  (`district_centroids`, part of the fingerprint) as provenance for the next load
  (Q239). The places routes already publish `centroid`, so the portal reads the point
  from `GET /api/v1/places` and `/places/{id}` (JSON `centroid`, GeoJSON `Point`) with
  no change to the public schema.
- **Edges.** `ShapelySharedEdgeCalculator` (port `SharedEdgeCalculator`), for each pair
  of districts within 1e-5° (~1 m): snap the second polygon to the first, intersect
  the outlines, merge into lines, simplify with Douglas-Peucker at 5e-4° (~50 m; end
  points are fixed, so edges still meet), then **remove everything within 5e-4° of the
  region's outline** (the boundary of the union of all region districts, linked or
  not, closed over gaps under 1e-5° with mitre joins, so every corner of the outline,
  the tips of sharp notches included, stays where the data has it), round to 5
  decimals and drop parts shorter than 1e-3° (~100 m). The cut comes last, so the
  guarantee holds for exactly what is published; each line ends about 50 m short of the
  outline. All values are **proposed** (Q233).
- **Coverage check.** Before anything is stored, the calculator validates the region's
  polygons as a coverage (Shapely `coverage_invalid_edges`, GEOS `CoverageValidator`)
  with a gap width equal to the clearance: overlaps, unmatched vertices on shared
  edges, and gaps between districts narrower than 5e-4° are digitising errors that lose
  stretches of shared edges. A load from an invalid coverage is refused
  (`BoundaryCoverageInvalidError`, exit code 1, the offending COD-AB codes in its
  details) unless the operator passes `--allow-invalid-coverage`; a `--dry-run` reports
  it without refusing. Wider gaps are unmapped land, not errors. COD-AB v01 passes.
- **Snapshot.** The edges, with their gazetteer codes (`null` for an unlinked
  district, Q234), the centroids above and the attribution become a `DistrictEdgeSet`
  in `district_edge_sets`, `district_edges` and `district_centroids` (migration
  `0020`). A content fingerprint (region, file digest, edges, centroids; not the
  download time) makes a reload of the same data a no-op; different data adds a new
  snapshot, and the newest is current. Snapshots are never edited.
- **Route.** `GET /api/v1/boundaries/district-edges`, anonymous: an
  `application/geo+json` `FeatureCollection` of `LineString`/`MultiLineString`
  features with `properties: {districts: [code|null, code|null], source_districts:
  [pcode, pcode]}` and a top-level `attribution` (`source`, `source_url`, `licence`,
  `licence_url`, `dataset_version`, `retrieved_at`), fully typed in OpenAPI through
  `response_class` (`GeoJsonResponse`). `Cache-Control: public, max-age=86400`, a strong
  `ETag` naming the snapshot, `304` on a matching `If-None-Match`, decided from the
  current snapshot's id alone (one indexed row), before any edge is read. Both headers
  are documented on the `200` and `304` responses in OpenAPI. With no snapshot:
  `200`, empty `features`, `attribution: null`, `max-age=300`, no `ETag` (Q235).

The real archive (COD-AB PAK v01, valid on 2022-09-09) gives 14 districts, 10 linked,
29 edges (11 touching an unlinked district), 2 209 positions and a 48 863-byte body.

### Consequences

- Good, because the outline is removed by construction and tested as a property on
  random coverages, so no data problem can put a border on the map.
- Good, because a changed or tampered archive fails the checksum instead of changing
  the map silently, and re-pinning is a reviewed change to one file.
- Good, because the request path is one indexed read of a small snapshot; the payload
  is about a third of the budget.
- Good, because places now have real footprints for later point-in-district lookups,
  and every linked district has a centroid inside it for reports placed by name.
- Bad, because `Place.geometry` now holds polygons whose outer edges are the Line of
  Control and international borders; any future read model, export or route exposing
  footprints would break the editorial rule (Q238).
- Bad, because a linked fixture takes COD-AB's post-split footprint (Ghizer without
  Gupis-Yasin, Q231), and four COD-AB districts have no gazetteer place yet (Q230).
- Bad, because lines stop about 50 m short of the outline, and per-edge simplification
  could in theory make two neighbouring edges cross near a junction.
- Bad, because CC BY-IGO data now sits beside CC BY 4.0 data (ADR 0010, Q236).
- Bad, because a release with digitising errors cannot be published without an explicit
  operator override, and the computed lines (so the snapshot fingerprint) depend on the
  GEOS version: a dependency upgrade may publish a new, equivalent snapshot (Q233).

## Pros and cons of the options

### Polygons in `Place.geometry`

- Good, because the domain already models a place's footprint, waiting for exactly
  this source (Q21, Q42), with validation, events and idempotent updates.
- Bad, because the place does not record where its footprint came from; the snapshot
  attribution and the source file do.

### A dedicated boundary table

- Good, because provenance per polygon and several datasets side by side.
- Bad, because a second geometry for the same place, a new aggregate and repository,
  and nothing reads it yet.

### Edges at load time with Shapely

- Good, because the computation, the editorial cut and its test run in one place,
  without a database, on the same code path as production.
- Bad, because the snapshot must be reloaded when the parameters change.

### Edges on request in PostGIS

- Good, because no stored derivative.
- Bad, because every API process recomputes; the outline cut and its property test
  would live in SQL; the cache would need invalidation.

### COD-AB `pak_adminlines.geojson`

- Good, because already line work.
- Bad, because it relies on the publisher's line classification to exclude the Line of
  Control and international boundaries; one misclassified segment would draw a border.

## More information

- HDX dataset `cod-ab-pak`, "Pakistan - Subnational Administrative Boundaries", source
  World Food Programme SDI, organisation OCHA Field Information Services Section;
  licence as stated on HDX (checked 2026-10-05): "Creative Commons Attribution for
  Intergovernmental Organisations (CC BY-IGO)", licence URL
  `http://creativecommons.org/licenses/by/3.0/igo/legalcode` (stored with `https`).
- Pinned resource `pak_admin_boundaries.geojson.zip` (29 097 223 bytes, HDX
  last-modified 2026-08-14), SHA-256
  `dc56da20dc578b01617f219f13a37e3be3beea78018adc902a6dee9d08dee45c`.
- Open questions Q1, Q42 (answered), Q230 to Q239 in `docs/open-questions.md`.
- Data dictionary: `docs/data-dictionary/geography.md`, "District boundaries".
