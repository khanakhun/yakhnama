# Data dictionary — conventions

This directory documents every public field Yakhnama stores or exports: its type, unit,
meaning, provenance and the version it was introduced in. There are no domain fields
documented yet — Phase 0 has no domain model — so this page records the conventions every
future entry follows, per the Definition of Done in `AGENTS.md` §9 ("The data dictionary
... covers every new field, unit and meaning").

## Layout

- One file per module: `docs/data-dictionary/<module>.md` (for example
  `docs/data-dictionary/reports.md`), created the first time that module gets a
  documented field.
- This `README.md` holds conventions only and is never module-specific.

## Table columns

Every field is a row in a Markdown table with exactly these columns:

| field | type | unit | meaning | provenance | since |
|-------|------|------|---------|------------|-------|

- **field** — the field's name exactly as it appears in the API schema or export column.
- **type** — its Pydantic/JSON type (`str`, `int`, `float`, `datetime`, `UUID`, a named
  enum, a `geojson-pydantic` geometry type, …).
- **unit** — the SI unit for a measurement (`m`, `m^3/s`, …), or `count` for a
  dimensionless count (people, structures, …), or `—` for non-measurement fields.
- **meaning** — one or two sentences a non-engineer can understand: what the field
  represents and any constraint that matters (nullable, bounded, append-only, …).
- **provenance** — where the value comes from: a reporter, a moderator decision, a named
  external source, or a computed/derived value (and, if derived, from what).
- **since** — the version or phase the field was introduced in (for example `v0.2.0` or
  `Phase 3`), so a consumer can tell whether it is safe to depend on.

## Conventions every field follows

- **Timestamps** are UTC, timezone-aware, and every timestamp is stored beside a
  `DatePrecision` (`exact | hour | day | month | season | year`) that says how precisely
  the date is actually known — a `DatePrecision` of `month` means the day-of-month in the
  timestamp is a placeholder, not a fact.
- **Units** are SI in storage (metres, cubic metres per second, …), or `count` for
  dimensionless counts (people, structures, …); a field's `unit` column always names it
  explicitly, even when it looks obvious.
- **Geometry** is WGS84 (EPSG:4326), per `geojson-pydantic` types.
- **Identifiers** are UUIDv7.
- **Confidence** — any uncertain number (an impact figure, a derived estimate) carries a
  `Confidence` level (`low | medium | high`) alongside its `provenance`; a number without
  a source is never published.

## Adding an entry

Follow the `add-entity`, `add-impact-metric` or `add-hazard-type` skill for the workflow;
each one lists updating the data dictionary as a required step, and the Definition of Done
checks it (`AGENTS.md` §9).
