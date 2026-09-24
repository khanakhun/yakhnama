# Yakhnama

*Chronicle of the ice: `yakh` is Persian for ice, `nāma` a written record.*

Yakhnama is the Python backend and system of record for an open platform documenting
natural hazards and disasters in high-mountain regions, starting in Gilgit-Baltistan,
Pakistan. This site is the technical documentation; the project's mission, quick start and
contributor guide live in the repository's `README.md` and `CONTRIBUTING.md`.

- **[Architecture](architecture/README.md)** — the modular-monolith and hexagonal-layer
  diagrams, the module map, and how reads, writes and errors flow.
- **[API conventions](architecture/api.md)** — authentication, Problem Details, pagination,
  idempotency, `ETag`/`If-Match`, content negotiation, rate limiting and the route table.
- **[Authentication](architecture/auth.md)** — the OIDC token flow, the development
  Keycloak realm and the backend's validation rules.
- **[Data exchange](architecture/exchange.md)** — exporting events, claims and reports
  (JSON, GeoJSON, CSV, GeoParquet) and importing a historical backfill.
- **[Data ingestion](architecture/ingestion.md)** — the dataset catalog, the Template
  Method ingestion pipeline, observations and the STAC-aligned raster asset catalog.
- **[Data sources](architecture/data-sources.md)** — the implemented fixture source and
  every documented, not-yet-implemented candidate source.
- **[Architecture decision records](adr/README.md)** — the recorded reasoning behind the
  structural choices, MADR format.
- **[Data dictionary](data-dictionary/README.md)** — conventions for documenting every
  public field: type, unit, meaning, provenance, and the version it shipped in.
- **[Open questions](open-questions.md)** — domain and product decisions not yet settled,
  with a proposed default and whether work is blocked on the answer.
- **[Plans](plans/phase-0.md)** — the phased implementation plans, one per
  `docs/plans/phase-N.md`, each approved by the maintainer before its code is written.

Repository: [github.com/khanakhun/yakhnama](https://github.com/khanakhun/yakhnama).
Code is licensed [Apache-2.0](https://github.com/khanakhun/yakhnama/blob/main/LICENSE)
(pending maintainer confirmation, see ADR 0010).
