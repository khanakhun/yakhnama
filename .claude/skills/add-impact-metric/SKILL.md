---
name: add-impact-metric
description: Add an impact metric to the registry - a data/reference/impact_metrics.yaml entry with code, SI unit, category, Sendai indicator and DesInventar mapping, tests that the registry loads and its units are recognised, and the data dictionary.
---

# add-impact-metric

## When to use

- An impact claim needs a metric that `data/reference/impact_metrics.yaml` does not have
  (for example `people_displaced`).
- Changing the unit or meaning of an existing metric is never done in place: retire it
  and add a new code.

## Preconditions

- The impacts module and `ImpactMetricCatalog` (`domain/catalog.py`) exist; create the
  model once from the template below if not. (The module name `impacts` is an
  assumption; use the real one.)
- The metric's meaning, unit and mappings are sourced. The Sendai indicator must come
  from the UNDRR Sendai Framework indicator list and the DesInventar variable from the
  DesInventar documentation. If either is uncertain, leave it `null` and write an open
  question; never guess a mapping.
- The unit is an SI (or SI-derived) symbol already in `SiUnit`. A new unit is a separate,
  reviewed change to `SiUnit`. Non-SI quantities (currency, for example) need an ADR.
- `pyyaml` and `types-pyyaml` are declared (see `add-hazard-type`).

## Owning subagent

`domain-modeler` (YAML, catalog model, unit tests, data dictionary). The reference-data
test lives in `tests/architecture/`; coordinate with `architect`.

## Files

| Path | Create/modify | Purpose |
|------|---------------|---------|
| `data/reference/impact_metrics.yaml` | modify | The new entry (and category if new) |
| `src/yakhnama/modules/impacts/domain/catalog.py` | create once | Validation model and `SiUnit` |
| `tests/unit/modules/impacts/domain/test_catalog.py` | create once / modify | Model rules |
| `tests/architecture/test_reference_data.py` | modify | Registry loads; units recognised; new code present |
| `docs/data-dictionary/impacts.md` | modify | Code, unit, meaning, mappings |
| `docs/open-questions.md` | modify | Uncertain mappings or definitions |

## Steps

1. Check the code is new and not retired: `grep -n "code: <code>"
   data/reference/impact_metrics.yaml`.
2. Add the entry: `code` (snake_case), `category` (existing category code), `unit` (from
   `SiUnit`; `"1"` for counts), `labels` (`en` required), `sendai_indicator` (code such as
   `B-1`, or `null`), `desinventar_variable` (or `null`), `status: active`,
   `retired_reason: null`.
3. Add the tests below (the new code is present; units recognised).
4. Update the data dictionary with the exact meaning: what is counted, over what period,
   and what is excluded.
5. Write open questions for any `null` mapping or uncertain definition.
6. Run the checks.

## Templates

### `data/reference/impact_metrics.yaml` (example entry)
```yaml
# Impact metric registry. Validated by ImpactMetricCatalog
# (src/yakhnama/modules/impacts/domain/catalog.py) in
# tests/architecture/test_reference_data.py.
#
# Rules: codes are never removed or reused; a metric's unit and meaning never change
# once claims reference it. Units are ASCII SI symbols; "1" means a count.
# Leave sendai_indicator / desinventar_variable null unless a cited source confirms
# the mapping, and record the question in docs/open-questions.md.
schema_version: 1
categories:
  - code: people
    # EXAMPLE ONLY: category scheme to be confirmed.
    labels:
      en: People
metrics:
  - code: people_displaced
    category: people
    unit: "1"
    labels:
      en: People displaced
    # EXAMPLE ONLY: mappings unconfirmed, see docs/open-questions.md.
    sendai_indicator: null
    desinventar_variable: null
    status: active
    retired_reason: null
```

### `src/yakhnama/modules/impacts/domain/catalog.py` (create once)
```python
"""The impact metric registry as versioned reference data.

Patterns: Value Object, Registry.

``data/reference/impact_metrics.yaml`` is validated against ``ImpactMetricCatalog``.
Impact claims store ``metric code + value`` (long and narrow), so a metric's unit
and meaning must never change once claims reference it; retire and add a new code.
"""

from collections import Counter
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MetricCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
LanguageTag = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-[A-Z]{2})?$"),
]
Label = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
Labels = Annotated[dict[LanguageTag, Label], Field(min_length=1, max_length=32)]
# Sendai Framework indicator codes: target letter A-G, number, optional suffix.
SendaiIndicator = Annotated[str, StringConstraints(pattern=r"^[A-G]-[0-9]{1,2}[a-z]?$")]
DesInventarVariable = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$"),
]
RetirementReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]

# ASCII SI symbols accepted in storage; "1" is the unit of a pure count. Extend only
# with SI base or derived units, in a reviewed change of its own.
SiUnit = Literal["1", "m", "m2", "m3", "kg", "s", "m/s", "m3/s"]


class MetricStatus(StrEnum):
    """Lifecycle status of a metric code.

    Implements: Value Object.
    """

    ACTIVE = "active"
    RETIRED = "retired"


class ImpactCategory(BaseModel):
    """A grouping of impact metrics.

    Implements: Value Object.

    Attributes:
        code: Stable category code.
        labels: Display labels keyed by BCP 47 tag; ``en`` is required.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode
    labels: Labels


class ImpactMetricDefinition(BaseModel):
    """One entry of the impact metric registry.

    Implements: Value Object.

    Attributes:
        code: Stable metric code, for example ``"people_displaced"``.
        category: Code of an ``ImpactCategory`` in the same file.
        unit: SI unit of stored values.
        labels: Display labels keyed by BCP 47 tag; ``en`` is required.
        sendai_indicator: Sendai Framework indicator the metric feeds, if any.
        desinventar_variable: DesInventar variable the metric maps to, if any.
        status: ``active`` or ``retired``.
        retired_reason: Required when ``status`` is ``retired``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode
    category: MetricCode
    unit: SiUnit
    labels: Labels
    sendai_indicator: SendaiIndicator | None = None
    desinventar_variable: DesInventarVariable | None = None
    status: MetricStatus = MetricStatus.ACTIVE
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
        is_retired = self.status is MetricStatus.RETIRED
        if is_retired != (self.retired_reason is not None):
            message = f"{self.code}: retired_reason is required iff status is retired"
            raise ValueError(message)
        return self


class ImpactMetricCatalog(BaseModel):
    """The whole impact metric registry file.

    Implements: Registry.

    Attributes:
        schema_version: Version of this file's structure.
        categories: Every category.
        metrics: Every metric ever defined, including retired ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    categories: tuple[ImpactCategory, ...]
    metrics: tuple[ImpactMetricDefinition, ...]

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        """Require unique codes and categories that exist.

        Returns:
            The validated catalog.

        Raises:
            ValueError: If a code repeats or a category is unknown.
        """
        codes = [entry.code for entry in self.categories] + [
            entry.code for entry in self.metrics
        ]
        duplicates = sorted(code for code, count in Counter(codes).items() if count > 1)
        if duplicates:
            message = f"duplicate category or metric codes: {duplicates}"
            raise ValueError(message)
        known = {category.code for category in self.categories}
        unknown = sorted(
            metric.code for metric in self.metrics if metric.category not in known
        )
        if unknown:
            message = f"metrics with unknown category: {unknown}"
            raise ValueError(message)
        return self

    def get(self, code: str) -> ImpactMetricDefinition | None:
        """Return the metric with ``code``, or ``None``.

        Args:
            code: A metric code.

        Returns:
            The definition, active or retired, or ``None``.
        """
        return next((entry for entry in self.metrics if entry.code == code), None)
```

### `tests/unit/modules/impacts/domain/test_catalog.py`
```python
"""Unit tests for the impact metric registry model."""

from typing import Any

import pydantic
import pytest

from yakhnama.modules.impacts.domain.catalog import (
    ImpactMetricCatalog,
    ImpactMetricDefinition,
)

PEOPLE_DISPLACED: dict[str, Any] = {
    "code": "people_displaced",
    "category": "people",
    "unit": "1",
    "labels": {"en": "People displaced"},
}


def test_impact_metric_definition_with_non_si_unit_raises_validation_error() -> None:
    payload = PEOPLE_DISPLACED | {"unit": "persons"}

    with pytest.raises(pydantic.ValidationError):
        ImpactMetricDefinition.model_validate(payload)


def test_impact_metric_definition_without_english_label_raises_validation_error() -> (
    None
):
    payload = PEOPLE_DISPLACED | {"labels": {"ur": "x"}}

    with pytest.raises(pydantic.ValidationError):
        ImpactMetricDefinition.model_validate(payload)


@pytest.mark.parametrize("indicator", ["A2", "H-1", "a-1", "B-123"])
def test_impact_metric_definition_with_malformed_sendai_code_raises_validation_error(
    indicator: str,
) -> None:
    payload = PEOPLE_DISPLACED | {"sendai_indicator": indicator}

    with pytest.raises(pydantic.ValidationError):
        ImpactMetricDefinition.model_validate(payload)


def test_impact_metric_catalog_with_unknown_category_raises_validation_error() -> None:
    payload = {"schema_version": 1, "categories": [], "metrics": [PEOPLE_DISPLACED]}

    with pytest.raises(pydantic.ValidationError):
        ImpactMetricCatalog.model_validate(payload)


def test_impact_metric_catalog_get_with_known_code_returns_definition() -> None:
    catalog = ImpactMetricCatalog.model_validate(
        {
            "schema_version": 1,
            "categories": [{"code": "people", "labels": {"en": "People"}}],
            "metrics": [PEOPLE_DISPLACED],
        },
    )

    definition = catalog.get("people_displaced")

    assert definition is not None
    assert definition.unit == "1"
```

### `tests/architecture/test_reference_data.py` — functions to append
```python
"""Append to ``tests/architecture/test_reference_data.py``."""

from pathlib import Path
from typing import Any, get_args

import yaml

from yakhnama.modules.impacts.domain.catalog import ImpactMetricCatalog, SiUnit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPOSITORY_ROOT / "data" / "reference"


def _load(name: str) -> Any:  # noqa: ANN401  # reason: YAML is untyped until validated
    """Parse one reference YAML file."""
    return yaml.safe_load((REFERENCE / name).read_text(encoding="utf-8"))


def test_impact_metrics_yaml_validates_against_catalog() -> None:
    raw = _load("impact_metrics.yaml")

    catalog = ImpactMetricCatalog.model_validate(raw)

    assert catalog.metrics


def test_impact_metrics_yaml_uses_only_recognised_si_units() -> None:
    catalog = ImpactMetricCatalog.model_validate(_load("impact_metrics.yaml"))

    units = {metric.unit for metric in catalog.metrics}

    assert units <= set(get_args(SiUnit))


def test_impact_metrics_yaml_contains_people_displaced() -> None:
    catalog = ImpactMetricCatalog.model_validate(_load("impact_metrics.yaml"))

    definition = catalog.get("people_displaced")

    assert definition is not None
```

When appending to the existing file, keep one `_load` helper and one `REFERENCE`
constant, and merge the imports.

### `docs/data-dictionary/impacts.md` — row to add

```markdown
| Metric code | Category | Unit | Meaning | Sendai | DesInventar | Status |
|-------------|----------|------|---------|--------|-------------|--------|
| `people_displaced` | people | 1 (count) | EXAMPLE ONLY: definition to be confirmed | — (open question) | — (open question) | active |
```

### `docs/open-questions.md` — entry format

Use the next free number and the file's section format:

```markdown
---

## Qn — Definition and mappings of `people_displaced`

**Question:** Which Sendai indicator and which DesInventar variable does
`people_displaced` map to, and what exactly counts as displaced (duration, evacuation
versus relocation)?

**Why it matters:** Exports aligned to Sendai and DesInventar would misreport the figure,
and a metric's meaning cannot change once claims reference it.

**Proposed default:** Leave both mappings `null` and document the definition as
provisional in the data dictionary.

**Blocking:** no

**Status:** open
```

## Required tests

- `test_impact_metrics_yaml_validates_against_catalog`
- `test_impact_metrics_yaml_uses_only_recognised_si_units`
- `test_impact_metrics_yaml_contains_<code>` for the new code
- Model rules (once): `test_impact_metric_definition_with_non_si_unit_raises_validation_error`,
  `test_impact_metric_definition_without_english_label_raises_validation_error`,
  `test_impact_metric_definition_with_malformed_sendai_code_raises_validation_error`,
  `test_impact_metric_catalog_with_unknown_category_raises_validation_error`,
  `test_impact_metric_catalog_get_with_known_code_returns_definition`

## Checks

```bash
poetry run poe format
poetry run poe lint
poetry run poe typecheck
poetry run pytest tests/unit/modules/impacts tests/architecture/test_reference_data.py -q
poetry run poe test-unit
poetry run poe test-api             # runs tests/architecture
poetry run poe check
```

## Definition of Done

- [ ] New code; existing category; SI unit from `SiUnit`; `en` label.
- [ ] Sendai and DesInventar mappings sourced, or `null` with an open question.
- [ ] Registry tests pass; the new code is asserted present.
- [ ] Data dictionary states the exact meaning and unit.
- [ ] `standards-reviewer` approved.
- [ ] Conventional Commit, for example `feat(impacts): add people_displaced metric`.
- [ ] `poetry run poe check` passes.

## Pitfalls

- **Changing a unit in place.** Stored claims keep the old unit's numbers; retire and add
  a new code instead.
- **Counts versus rates.** A per-capita value is a different metric from a count; never
  mix them under one code.
- **Currency is not SI.** Economic loss needs an ADR (currency, price year) before a
  metric exists.
- **Guessed mappings.** A plausible Sendai or DesInventar mapping that nobody checked is
  worse than `null`; it will be exported as fact.
