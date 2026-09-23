# Reference data

Versioned YAML files that seed the registries and the place hierarchy. Each file is
parsed with `yaml.safe_load` and validated against a Pydantic model before anything is
stored; the tests in `tests/unit/data/` prove every file round-trips through its model.

| File | Model | Content |
|------|-------|---------|
| `hazard_types.yaml` | `HazardTypeReferenceFile` (`modules/hazards/domain/reference.py`) | IRDR-aligned hazard taxonomy: abstract parents and the seven attribute-bearing types |
| `impact_metrics.yaml` | `ImpactMetricReferenceFile` (`modules/impacts/domain/reference.py`) | Impact metric registry: counts and SI measurements, no monetary metrics yet |
| `languages.yaml` | none yet (test-local model in `tests/unit/data/test_languages.py`) | Languages and proposed default scripts for names and labels |
| `admin_hierarchy_gb.yaml` | `PlaceReferenceFile` (`modules/geography/domain/reference.py`) | **Fixture**: Pakistan, Gilgit-Baltistan, its divisions and districts, no geometry |

## Header

Every file starts with the same four fields:

| Field | Meaning |
|-------|---------|
| `schema_version` | Version of the file's structure. Only `1` exists. Bumped only together with the model that reads it. |
| `data_version` | Version of the content: an ISO date (`"2026-09-23"`, quoted so YAML keeps it a string) or an integer-like string. |
| `source` | Citation for the file as a whole, saying which parts are sourced and which are proposed. |
| `licence` | Licence of the content. `CC-BY-4.0` is proposed (ADR 0010, open question Q4) until the maintainer confirms it. |

## Versioning

- Any change to an entry bumps `data_version` to the date of the change.
- A change of structure bumps `schema_version` and the model in the same commit.
- Codes are never removed, renamed or reused. A hazard type or metric that is no longer
  wanted is set to `status: retired` with a `retirement` reason (and `replaced_by` when a
  successor exists). A metric's unit, kind, category or aggregation never changes in
  place: retire it and add a new code.

## Provenance: `source`, `status` and `proposed`

- Every entry has a `source`. It is either a citation (for example
  `IRDR Peril Classification and Hazard Glossary, 2014`) or the word `proposed`, which
  means a contributor suggested it and the maintainer has not confirmed it.
- Hazard types and metrics carry `status: active | retired`; the lifecycle, not the
  provenance.
- Places carry `status: sourced | proposed | fixture`. `fixture` marks development and
  test data that is not a claim about the world; every entry in
  `admin_hierarchy_gb.yaml` is a fixture until the boundary source is chosen (open
  question Q1).
- Languages carry `status: proposed` until the code list and scripts are confirmed
  (open question Q5).
- `notes` explains placements, candidate mappings that were deliberately left unset,
  and the open question to settle.

## Labels and names in other languages

English labels and names may ship without a per-label citation. A label or name in any
other language (Urdu, Shina, Burushaski, Balti, Wakhi, Khowar, or an alternative
spelling) needs a cited `source`: on the name itself for places, and a non-`proposed`
entry `source` for hazard types and metrics. Nothing in another language is added on a
guess (open question Q2).

## Adding entries

- A hazard type: follow `.claude/skills/add-hazard-type/SKILL.md`. Its
  `attributes_schema`, if any, must be a code in `DEFAULT_REGISTRY`.
- An impact metric: follow `.claude/skills/add-impact-metric/SKILL.md`. Set `sendai` and
  `desinventar` only when the mapping is certain; otherwise leave them null and write
  the candidate in `notes`. Monetary metrics need an ADR first.
- Places: only from the chosen boundary source, with `status: sourced` and a `source`.
- Update the matching page under `docs/data-dictionary/` and run
  `poetry run pytest tests/unit/data`.
