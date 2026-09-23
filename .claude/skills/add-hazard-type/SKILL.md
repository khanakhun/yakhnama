---
name: add-hazard-type
description: Add a hazard type to the taxonomy - a data/reference/hazard_types.yaml entry with IRDR alignment, parent, language-tagged labels and status, an attribute schema registered in the hazards discriminated union, tests, the data dictionary and an open question for anything uncertain.
---

# add-hazard-type

## When to use

- A hazard that Yakhnama must record has no code in `data/reference/hazard_types.yaml`.
- Retiring a code uses the same file (status change plus reason), never deletion.

## Preconditions

- The hazards module, `HazardTypeCatalog` (`domain/catalog.py`) and the attribute
  registry (`domain/attributes.py`) exist. If they do not, create them once from the
  templates below.
- The definition is sourced: the IRDR peril classification (IRDR DATA Publication
  No. 1, 2014) for `irdr`, and a cited source for every non-English label. If any part is
  uncertain, write the open question first and mark the entry `EXAMPLE ONLY` until the
  maintainer answers. Never invent a definition, threshold or local name.
- `pyyaml` is a declared dependency and `types-pyyaml` is in the dev group (lead adds
  them with `poetry add pyyaml` and `poetry add --group dev types-pyyaml`). Today PyYAML
  is only present transitively through the docs group, and mypy reports missing stubs.

## Owning subagent

`domain-modeler` (YAML, domain classes, unit tests, data dictionary, open questions).
The reference-data test lives in `tests/architecture/`; coordinate with `architect`, who
owns that directory.

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `data/reference/hazard_types.yaml` | modify | The new entry |
| `src/yakhnama/modules/hazards/domain/attributes.py` | modify | Attribute schema + union member + registry entry |
| `src/yakhnama/modules/hazards/domain/catalog.py` | create once | Validation model of the YAML file |
| `tests/unit/modules/hazards/domain/test_attributes.py` | modify | Registry and schema tests |
| `tests/architecture/test_reference_data.py` | create once / modify | YAML validates; registry matches YAML |
| `docs/data-dictionary/hazards.md` | modify | Code, meaning, attributes and units |
| `docs/open-questions.md` | modify | Entry for every uncertain definition or name |

## Steps

1. Check the code is new: `grep -n "code: <code>" data/reference/hazard_types.yaml`. A
   retired code is never reused; pick a new one.
2. Add the YAML entry: `code` (snake_case, ≤ 64), `parent` (existing code or `null`),
   `irdr.family` / `irdr.main_event` / `irdr.peril`, `labels` keyed by BCP 47 tag (`en`
   required; `ur`, `scl`, `bsk`, `bft`, `wbl`, `khw` and script subtags only from a cited
   source), `status: active`, `retired_reason: null`.
3. If the hazard needs its own attributes, add a schema class deriving from
   `HazardAttributesBase` with `hazard_code: Literal["<code>"] = "<code>"`, SI units in
   field names (`..._square_metres`, `..._cubic_metres`), bounds on every number. Add it
   to the `HazardAttributes` union and to `HAZARD_ATTRIBUTE_SCHEMAS`. Do not touch other
   schemas.
4. Add tests (below). Update the data dictionary. Write the open question if anything is
   unconfirmed.
5. Run the checks. If a migration is needed (for example a CHECK constraint listing
   codes), follow `write-migration`; prefer validating codes against the registry in the
   application instead.

## Templates

### `data/reference/hazard_types.yaml` (example entries)
```yaml
# Hazard taxonomy reference data. Validated by HazardTypeCatalog
# (src/yakhnama/modules/hazards/domain/catalog.py) in
# tests/architecture/test_reference_data.py.
#
# Rules: codes are never removed or reused; retire with status + retired_reason.
# Labels are keyed by BCP 47 tag; add a non-English label only with a cited source.
schema_version: 1
hazard_types:
  - code: flood
    # EXAMPLE ONLY: IRDR placement to be confirmed.
    parent: null
    irdr:
      family: hydrological
      main_event: flood
      peril: null
    labels:
      en: Flood
    status: active
    retired_reason: null
  - code: glof
    # EXAMPLE ONLY: IRDR placement below is unverified. Confirm it against the IRDR
    # peril classification (2014) and record the source before committing
    # (docs/open-questions.md).
    parent: null
    irdr:
      family: climatological
      main_event: glacial_lake_outburst
      peril: null
    labels:
      en: Glacial lake outburst flood
    status: active
    retired_reason: null
  - code: landslide
    # EXAMPLE ONLY: IRDR placement to be confirmed.
    parent: null
    irdr:
      family: hydrological
      main_event: landslide
      peril: null
    labels:
      en: Landslide
    status: active
    retired_reason: null
```

The only fact above taken from the project itself is the English expansion of GLOF
(glossary). Everything marked `EXAMPLE ONLY` must be confirmed before merge.

### `src/yakhnama/modules/hazards/domain/catalog.py` (create once)
```python
"""The hazard taxonomy as versioned reference data.

Patterns: Value Object.

``data/reference/hazard_types.yaml`` is validated against ``HazardTypeCatalog``.
Codes are retired, never removed or reused, so historical events keep their
meaning.
"""

from collections import Counter
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from yakhnama.modules.hazards.domain.entities import (
    HazardTypeStatus,
    RetirementReason,
)
from yakhnama.modules.hazards.domain.value_objects import HazardCode

# BCP 47 subset: language, optional script, optional region ("en", "ur", "scl",
# "ur-Arab", "en-PK").
LanguageTag = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2})?$"),
]
Label = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
IrdrTerm = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


class IrdrFamily(StrEnum):
    """Top level of the IRDR peril classification (IRDR DATA Publication 1, 2014).

    Implements: Value Object.
    """

    GEOPHYSICAL = "geophysical"
    HYDROLOGICAL = "hydrological"
    METEOROLOGICAL = "meteorological"
    CLIMATOLOGICAL = "climatological"
    BIOLOGICAL = "biological"
    EXTRATERRESTRIAL = "extraterrestrial"


class IrdrAlignment(BaseModel):
    """Where a hazard type sits in the IRDR peril classification.

    Implements: Value Object.

    Attributes:
        family: IRDR family.
        main_event: IRDR main event, snake_case.
        peril: IRDR peril, or ``None`` when the type maps to a main event only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: IrdrFamily
    main_event: IrdrTerm
    peril: IrdrTerm | None = None


class HazardTypeDefinition(BaseModel):
    """One entry of the hazard taxonomy reference file.

    Implements: Value Object.

    Attributes:
        code: Stable code; never reused.
        parent: Code of the broader type, or ``None`` for a root.
        irdr: IRDR alignment.
        labels: Display labels keyed by BCP 47 language tag; ``en`` is required.
        status: ``active`` or ``retired``.
        retired_reason: Required when ``status`` is ``retired``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HazardCode
    parent: HazardCode | None = None
    irdr: IrdrAlignment
    labels: Annotated[dict[LanguageTag, Label], Field(min_length=1, max_length=32)]
    status: HazardTypeStatus = HazardTypeStatus.ACTIVE
    retired_reason: RetirementReason | None = None

    @model_validator(mode="after")
    def _check_entry(self) -> Self:
        """Require an English label and a reason exactly when retired.

        Returns:
            The validated entry.

        Raises:
            ValueError: If a rule is broken.
        """
        if "en" not in self.labels:
            message = f"{self.code}: an 'en' label is required"
            raise ValueError(message)
        is_retired = self.status is HazardTypeStatus.RETIRED
        if is_retired != (self.retired_reason is not None):
            message = f"{self.code}: retired_reason is required iff status is retired"
            raise ValueError(message)
        return self


class HazardTypeCatalog(BaseModel):
    """The whole hazard taxonomy file.

    Implements: Value Object.

    Attributes:
        schema_version: Version of this file's structure.
        hazard_types: Every hazard type ever defined, including retired ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    hazard_types: tuple[HazardTypeDefinition, ...]

    @model_validator(mode="after")
    def _check_tree(self) -> Self:
        """Require unique codes and parents that exist.

        Returns:
            The validated catalog.

        Raises:
            ValueError: If a code repeats or a parent is unknown.
        """
        counts = Counter(entry.code for entry in self.hazard_types)
        duplicates = sorted(code for code, count in counts.items() if count > 1)
        if duplicates:
            message = f"duplicate hazard codes: {duplicates}"
            raise ValueError(message)
        unknown = sorted(
            entry.code
            for entry in self.hazard_types
            if entry.parent is not None and entry.parent not in counts
        )
        if unknown:
            message = f"hazard types with unknown parent: {unknown}"
            raise ValueError(message)
        return self

    @property
    def codes(self) -> frozenset[str]:
        """Return every code in the catalog, active or retired."""
        return frozenset(entry.code for entry in self.hazard_types)
```

### `src/yakhnama/modules/hazards/domain/attributes.py`
```python
"""Hazard-specific attribute schemas, selected by hazard code.

Patterns: Strategy, Registry (discriminated union).

Each hazard type that needs extra attributes gets one schema class here, a member
in ``HazardAttributes`` and an entry in ``HAZARD_ATTRIBUTE_SCHEMAS``. Adding a
hazard never changes an existing schema.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
GlimsId = Annotated[str, StringConstraints(min_length=1, max_length=32)]


class HazardAttributesBase(BaseModel):
    """Common configuration of every hazard attribute schema.

    Implements: Strategy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class GlofAttributes(HazardAttributesBase):
    """Attributes recorded for glacial lake outburst floods (example fields only).

    Implements: Strategy.

    Attributes:
        hazard_code: Discriminator; always ``"glof"``.
        glims_lake_id: GLIMS identifier of the source lake, if known.
        lake_area_square_metres: Lake area before the outburst, in m².
    """

    hazard_code: Literal["glof"] = "glof"
    glims_lake_id: GlimsId | None = None
    lake_area_square_metres: NonNegativeFloat | None = None


class LandslideAttributes(HazardAttributesBase):
    """Attributes recorded for landslides (example fields only).

    Implements: Strategy.

    Attributes:
        hazard_code: Discriminator; always ``"landslide"``.
        volume_cubic_metres: Displaced volume, in m³.
    """

    hazard_code: Literal["landslide"] = "landslide"
    volume_cubic_metres: NonNegativeFloat | None = None


HazardAttributes = Annotated[
    GlofAttributes | LandslideAttributes,
    Field(discriminator="hazard_code"),
]

HAZARD_ATTRIBUTE_SCHEMAS: Mapping[str, type[HazardAttributesBase]] = MappingProxyType(
    {
        "glof": GlofAttributes,
        "landslide": LandslideAttributes,
    },
)
```

The attribute fields shown are **example only**; each real field needs a definition and
unit in the data dictionary and, if uncertain, an open question.

### `tests/unit/modules/hazards/domain/test_attributes.py`
```python
"""Unit tests for the hazard attribute registry."""

from typing import get_args

import pydantic
import pytest
from hypothesis import given
from hypothesis import strategies as st

from yakhnama.modules.hazards.domain.attributes import (
    HAZARD_ATTRIBUTE_SCHEMAS,
    GlofAttributes,
    HazardAttributes,
)

HAZARD_ATTRIBUTES_ADAPTER: pydantic.TypeAdapter[HazardAttributes] = (
    pydantic.TypeAdapter(HazardAttributes)
)
AREAS = st.floats(min_value=0.0, allow_nan=False, allow_infinity=False)


def test_hazard_attribute_registry_matches_union_members() -> None:
    union_members = get_args(get_args(HazardAttributes)[0])

    registered = set(HAZARD_ATTRIBUTE_SCHEMAS.values())

    assert registered == set(union_members)


def test_hazard_attribute_registry_keys_match_discriminators() -> None:
    mismatched = [
        code
        for code, schema in HAZARD_ATTRIBUTE_SCHEMAS.items()
        if schema.model_fields["hazard_code"].default != code
    ]

    assert mismatched == []


@given(area=AREAS)
def test_glof_attributes_with_valid_area_round_trips_through_union(
    area: float,
) -> None:
    payload = {"hazard_code": "glof", "lake_area_square_metres": area}

    attributes = HAZARD_ATTRIBUTES_ADAPTER.validate_python(payload)

    assert isinstance(attributes, GlofAttributes)
    assert attributes.lake_area_square_metres == area


def test_glof_attributes_with_negative_area_raises_validation_error() -> None:
    payload = {"hazard_code": "glof", "lake_area_square_metres": -1.0}

    with pytest.raises(pydantic.ValidationError):
        HAZARD_ATTRIBUTES_ADAPTER.validate_python(payload)


def test_hazard_attributes_with_unknown_code_raises_validation_error() -> None:
    payload = {"hazard_code": "not_registered"}

    with pytest.raises(pydantic.ValidationError):
        HAZARD_ATTRIBUTES_ADAPTER.validate_python(payload)
```

### `tests/architecture/test_reference_data.py`
```python
"""Every reference data file in ``data/reference`` validates against its model."""

from pathlib import Path
from typing import Any

import yaml

from yakhnama.modules.hazards.domain.attributes import HAZARD_ATTRIBUTE_SCHEMAS
from yakhnama.modules.hazards.domain.catalog import HazardTypeCatalog
from yakhnama.modules.hazards.domain.entities import HazardTypeStatus

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPOSITORY_ROOT / "data" / "reference"


def _load(name: str) -> Any:  # noqa: ANN401  # reason: YAML is untyped until validated
    """Parse one reference YAML file."""
    return yaml.safe_load((REFERENCE / name).read_text(encoding="utf-8"))


def test_hazard_types_yaml_validates_against_catalog() -> None:
    raw = _load("hazard_types.yaml")

    catalog = HazardTypeCatalog.model_validate(raw)

    assert catalog.hazard_types


def test_hazard_attribute_schemas_exist_only_for_active_codes() -> None:
    catalog = HazardTypeCatalog.model_validate(_load("hazard_types.yaml"))
    active = {
        entry.code
        for entry in catalog.hazard_types
        if entry.status is HazardTypeStatus.ACTIVE
    }

    orphaned = set(HAZARD_ATTRIBUTE_SCHEMAS) - active

    assert orphaned == set()


def test_hazard_types_yaml_contains_glof() -> None:
    catalog = HazardTypeCatalog.model_validate(_load("hazard_types.yaml"))

    codes = catalog.codes

    assert "glof" in codes
```

### `docs/data-dictionary/hazards.md` — rows to add

```markdown
| Hazard code | Parent | IRDR (family / main event / peril) | Labels | Status | Source |
|-------------|--------|------------------------------------|--------|--------|--------|
| `glof` | — | climatological / glacial_lake_outburst / — (EXAMPLE ONLY) | en | active | IRDR 2014 (to confirm) |

| Attribute schema | Field | Type | Unit | Nullable | Meaning |
|------------------|-------|------|------|----------|---------|
| `GlofAttributes` | `glims_lake_id` | string (≤ 32) | — | yes | GLIMS id of the source lake (EXAMPLE ONLY) |
| `GlofAttributes` | `lake_area_square_metres` | float ≥ 0 | m² | yes | Lake area before the outburst (EXAMPLE ONLY) |
```

### `docs/open-questions.md` — entry format

Local-language names are already covered by **Q2 — Final hazard taxonomy labels and
local-language names**; reference Q2 from the YAML comment and the data dictionary
instead of opening a new question. A genuinely new uncertainty gets the next free
number, in the file's section format:

```markdown
---

## Qn — IRDR placement of the `glof` hazard type

**Question:** Where does `glof` sit in the IRDR peril classification (family, main event,
peril), and should it have a parent in our taxonomy?

**Why it matters:** A wrong placement mis-files every GLOF event in exports aligned to
IRDR or EM-DAT, and hazard codes cannot be reused once published.

**Proposed default:** `climatological / glacial_lake_outburst`, no parent, marked
EXAMPLE ONLY until confirmed against IRDR (2014).

**Blocking:** no

**Status:** open
```

## Required tests

- `test_hazard_attribute_registry_matches_union_members`
- `test_hazard_attribute_registry_keys_match_discriminators`
- `test_glof_attributes_with_valid_area_round_trips_through_union` (hypothesis)
- `test_glof_attributes_with_negative_area_raises_validation_error`
- `test_hazard_attributes_with_unknown_code_raises_validation_error`
- `test_hazard_types_yaml_validates_against_catalog`
- `test_hazard_attribute_schemas_exist_only_for_active_codes`
- `test_hazard_types_yaml_contains_<code>` for the new code

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run poe arch
poetry run pytest tests/unit/modules/hazards/domain tests/architecture/test_reference_data.py -q
poetry run poe test-unit
poetry run poe test-api             # runs tests/architecture
poetry run poe check
```

## Definition of Done

- [ ] New, never-used code; parent exists; `en` label present; every other label cited.
- [ ] IRDR alignment sourced, or marked `EXAMPLE ONLY` with an open question.
- [ ] Attribute schema (if any) registered in both the union and the registry; no other
      schema changed.
- [ ] Tests pass; the YAML validates.
- [ ] Data dictionary and open questions updated.
- [ ] `standards-reviewer` approved.
- [ ] Conventional Commit, for example `feat(hazards): add glof hazard type`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Deleting or renaming a code.** Historical events reference it. Retire it
  (`status: retired`, `retired_reason`) and add a new code.
- **Registry drift.** A schema in the union but not in `HAZARD_ATTRIBUTE_SCHEMAS` (or the
  reverse) is caught by the registry test; keep both edits in one change.
- **Units in names.** A float called `area` is ambiguous; name the SI unit.
- **Language tags.** Use BCP 47 (`ur`, `ur-Arab`, `scl`); do not invent tags for
  languages or scripts.
- **YAML typing.** `yaml.safe_load` returns untyped data; validate it with the Pydantic
  model immediately and never pass the raw structure on.
