"""Value objects of the impact metric registry.

An impact claim (Phase 3) stores ``metric code + value``, long and narrow, so the
metric decides what a value means: its kind (count, physical measurement or money),
its unit or currency, how claims combine, and its Sendai and DesInventar alignment.
The rules shared by the aggregate and the reference file live here so both enforce
exactly the same definition.

Every category, the Sendai code shape, the DesInventar field shape, the monetary
convention, the aggregation defaults and the ban on negative values are **proposed
defaults** awaiting the maintainer (``docs/data-dictionary/impacts.md``, open
questions); none is a sourced domain fact.

Patterns: Value Object.
"""

import math
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from yakhnama.shared_kernel.value_objects import SiUnit, is_known_unit

METRIC_CODE_PATTERN: Final = r"^[a-z][a-z0-9_]{1,63}$"

MetricCode = Annotated[str, StringConstraints(pattern=METRIC_CODE_PATTERN)]
"""A stable snake_case metric code (2 to 64 characters), never reused once retired."""

CURRENCY_CODE_PATTERN: Final = r"^[A-Z]{3}$"

CurrencyCode = Annotated[str, StringConstraints(pattern=CURRENCY_CODE_PATTERN)]
"""An ISO 4217 alphabetic currency code such as ``PKR``; only the shape is checked."""

DEFAULT_CURRENCY: Final = "PKR"
"""Proposed default currency of monetary metrics: nominal Pakistani rupees."""

COUNT_UNIT: Final = SiUnit.COUNT.value
"""The only unit a ``count`` metric may use."""

Aggregation = Literal["sum", "max", "latest"]
"""How the Phase 3 best-figure policy combines claims of one metric (proposed)."""

REQUIRED_LABEL_LANGUAGE: Final = "en"
"""Every metric carries an English label, so exports always have a readable name."""


class ImpactMetricRef(BaseModel):
    """A reference to an impact metric by its stable code.

    Other aggregates (impact claims in Phase 3) hold this instead of the metric
    itself, because the code, unlike the database id, is shared with the open
    dataset and never changes.

    Implements: Value Object.

    Attributes:
        code: The metric code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode


class MetricCategory(StrEnum):
    """The group a metric belongs to (proposed).

    Proposed alignment, to be checked against the UNDRR indicator list and the
    DesInventar documentation: ``human`` to Sendai targets A and B, ``economic`` to
    target C, ``infrastructure`` and ``services`` to target D, ``housing`` and
    ``agriculture`` to the housing and agricultural indicators and DesInventar
    effects, ``environment`` to effects no Sendai indicator covers.

    Implements: Value Object.
    """

    HUMAN = "human"
    ECONOMIC = "economic"
    INFRASTRUCTURE = "infrastructure"
    HOUSING = "housing"
    AGRICULTURE = "agriculture"
    ENVIRONMENT = "environment"
    SERVICES = "services"


class SendaiIndicator(BaseModel):
    """A Sendai Framework global indicator code such as ``A-1`` or ``C-2``.

    Only the shape is checked (target letter A to G, number, optional lower-case
    sub-letter); whether a code exists in the UNDRR indicator list, and whether a
    metric really maps to it, is a proposal recorded in the data dictionary.

    Implements: Value Object.

    Attributes:
        code: The indicator code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[A-G]-[0-9]{1,2}[a-z]?$")


class DesInventarField(BaseModel):
    """The DesInventar effect field a metric maps to (proposed).

    Implements: Value Object.

    Attributes:
        name: The DesInventar field name exactly as DesInventar spells it, 1 to 64
            characters with no surrounding whitespace.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^\S(.*\S)?$")


class ValueKind(StrEnum):
    """What kind of number a metric holds, which fixes its unit rule.

    - ``count``: whole non-negative numbers in unit ``count`` (people, houses).
    - ``measurement``: non-negative quantities in a registered SI unit
      (``KNOWN_UNITS``), for example ``square_metre`` of farmland.
    - ``monetary``: non-negative nominal amounts in an ISO 4217 currency, with no
      SI unit; the claim (Phase 3) records the price year and no conversion is done
      (proposed).

    Implements: Value Object.
    """

    COUNT = "count"
    MEASUREMENT = "measurement"
    MONETARY = "monetary"


class MetricStatus(StrEnum):
    """Lifecycle of a metric: codes are retired, never deleted or reused.

    Implements: Value Object.
    """

    ACTIVE = "active"
    RETIRED = "retired"


class RetirementReason(BaseModel):
    """Why a metric was retired and, if so, which metric replaces it.

    A metric's unit or meaning never changes in place; it is retired and a new code
    takes over, named in ``replaced_by`` so old claims can be read against the new
    definition.

    Implements: Value Object.

    Attributes:
        explanation: Human-readable reason, 1 to 1000 characters.
        replaced_by: Code of the successor metric, if there is one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    explanation: str = Field(min_length=1, max_length=1000)
    replaced_by: MetricCode | None = None


def definition_problems(
    value_kind: ValueKind, unit: str | None, currency: str | None
) -> tuple[str, ...]:
    """List every way a unit and currency contradict a value kind.

    Args:
        value_kind: The metric's kind of value.
        unit: The metric's unit, if any.
        currency: The metric's currency code, if any.

    Returns:
        One message per problem; empty when the definition is consistent.
    """
    problems: list[str] = []
    if value_kind is ValueKind.COUNT and unit != COUNT_UNIT:
        problems.append(f"a count metric must use unit {COUNT_UNIT!r}, not {unit!r}")
    if value_kind is ValueKind.MEASUREMENT:
        if unit is None:
            problems.append("a measurement metric needs a unit")
        elif unit == COUNT_UNIT:
            problems.append("a measurement metric must not use unit 'count'")
        elif not is_known_unit(unit):
            problems.append(f"unit {unit!r} is not a registered SI unit")
    if value_kind is ValueKind.MONETARY and unit is not None:
        problems.append("a monetary metric carries a currency, not a unit")
    if value_kind is ValueKind.MONETARY and currency is None:
        problems.append("a monetary metric needs a currency")
    if value_kind is not ValueKind.MONETARY and currency is not None:
        problems.append(f"a {value_kind.value} metric must not carry a currency")
    return tuple(problems)


def is_valid_metric_value(value_kind: ValueKind, value: float) -> bool:
    """Tell whether ``value`` is acceptable for a metric of ``value_kind``.

    Negative impacts are rejected (proposed): a metric counts or measures a loss or
    damage, and a decrease is expressed by a later, lower claim rather than a
    negative one.

    Args:
        value_kind: The metric's kind of value.
        value: The candidate value.

    Returns:
        ``True`` if the value is finite and non-negative and, for counts, whole.
    """
    if not math.isfinite(value) or value < 0:
        return False
    return value_kind is not ValueKind.COUNT or float(value).is_integer()


def default_aggregation(value_kind: ValueKind) -> Aggregation:
    """Return the proposed aggregation for a kind of value.

    Counts and money add up across disjoint claims; a measurement is assumed to be a
    peak (flooded area, lake volume) whose best figure is the largest claim.

    Args:
        value_kind: The metric's kind of value.

    Returns:
        ``"max"`` for measurements, ``"sum"`` otherwise.
    """
    return "max" if value_kind is ValueKind.MEASUREMENT else "sum"
