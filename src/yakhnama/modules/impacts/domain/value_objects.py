"""Value objects of the impact metric registry.

An impact claim (Phase 3) stores ``metric code + value``, long and narrow, so the
metric decides what a value means: its kind (count, physical measurement or money),
its unit or currency, how claims combine, and its Sendai and DesInventar alignment.
The rules shared by the aggregate and the reference file live here so both enforce
exactly the same definition.

Phase 3 adds the values impact claims, infrastructure assets and damage records hold:
``ClaimValue`` (one variant per ``ValueKind``), ``ClaimStatus``, the source type names
and their proposed ``SourceRank``, ``ClaimScope``, ``AssetKind``, ``OsmId`` and
``DamageLevel``.

Every category, the Sendai code shape, the DesInventar field shape, the monetary
convention, the aggregation defaults, the ban on negative values, the source ranking,
the asset kinds and the damage levels are **proposed defaults** awaiting the
maintainer (``docs/data-dictionary/impacts.md``, open questions); none is a sourced
domain fact.

Patterns: Value Object.
"""

import math
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import Annotated, Final, Literal, Self, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.text import safe_text
from yakhnama.shared_kernel.value_objects import Measurement, SiUnit, is_known_unit

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


# --------------------------------------------------------------------------- #
# Claim values (Phase 3)                                                      #
# --------------------------------------------------------------------------- #

MONETARY_MAX_DIGITS: Final = 20
"""Digits a monetary amount may have; far beyond any loss, and finite as a float."""

MONETARY_DECIMAL_PLACES: Final = 2
"""Decimal places of a monetary amount (proposed: paisa, cents)."""

EARLIEST_PRICE_YEAR: Final = 1900
LATEST_PRICE_YEAR: Final = 2100


class CountValue(BaseModel):
    """A whole, non-negative number of people, houses or other counted things.

    Implements: Value Object.

    Attributes:
        kind: Always ``"count"``; the union discriminator.
        count: The number, 0 or more.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["count"] = "count"
    count: int = Field(ge=0)

    @property
    def value_kind(self) -> ValueKind:
        """Return the metric value kind this value belongs to.

        Returns:
            ``ValueKind.COUNT``.
        """
        return ValueKind.COUNT

    @property
    def unit_or_currency(self) -> str:
        """Return what the number is expressed in, to compare with the metric.

        Returns:
            ``"count"``.
        """
        return COUNT_UNIT


class MeasurementValue(BaseModel):
    """A non-negative physical quantity in a registered SI unit.

    The non-negative rule is ``is_valid_metric_value`` itself, so a claim value and a
    metric agree on what is acceptable.

    Implements: Value Object.

    Attributes:
        kind: Always ``"measurement"``; the union discriminator.
        measurement: The quantity and its unit, never in unit ``count``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["measurement"] = "measurement"
    measurement: Measurement

    @field_validator("measurement", mode="after")
    @classmethod
    def _check_measurement(cls, measurement: Measurement) -> Measurement:
        if measurement.unit == COUNT_UNIT:
            message = "a measurement must not use unit 'count'; use a count value"
            raise ValueError(message)
        if not is_valid_metric_value(ValueKind.MEASUREMENT, measurement.value):
            message = "a measurement value must be finite and non-negative"
            raise ValueError(message)
        return measurement

    @property
    def value_kind(self) -> ValueKind:
        """Return the metric value kind this value belongs to.

        Returns:
            ``ValueKind.MEASUREMENT``.
        """
        return ValueKind.MEASUREMENT

    @property
    def unit_or_currency(self) -> str:
        """Return what the number is expressed in, to compare with the metric.

        Returns:
            The SI unit name.
        """
        return self.measurement.unit


class MonetaryValue(BaseModel):
    """A non-negative nominal amount of money in one currency and price year.

    ``Decimal`` because money is exact; no inflation or exchange-rate conversion is
    ever applied (proposed), so the price year travels with the amount.

    Implements: Value Object.

    Attributes:
        kind: Always ``"monetary"``; the union discriminator.
        amount: The nominal amount, 0 or more, at most two decimal places.
        currency: ISO 4217 code, equal to the metric's currency.
        price_year: The year whose prices the amount is expressed in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["monetary"] = "monetary"
    amount: Decimal = Field(
        ge=0,
        max_digits=MONETARY_MAX_DIGITS,
        decimal_places=MONETARY_DECIMAL_PLACES,
        allow_inf_nan=False,
    )
    currency: CurrencyCode
    price_year: int = Field(ge=EARLIEST_PRICE_YEAR, le=LATEST_PRICE_YEAR)

    @property
    def value_kind(self) -> ValueKind:
        """Return the metric value kind this value belongs to.

        Returns:
            ``ValueKind.MONETARY``.
        """
        return ValueKind.MONETARY

    @property
    def unit_or_currency(self) -> str:
        """Return what the number is expressed in, to compare with the metric.

        Returns:
            The ISO 4217 currency code.
        """
        return self.currency


ClaimValue = Annotated[
    CountValue | MeasurementValue | MonetaryValue, Field(discriminator="kind")
]
"""One claim's value: one variant per ``ValueKind``, told apart by ``kind``."""


class ClaimStatus(StrEnum):
    """Lifecycle of a claim or damage record: retracted, never deleted or edited.

    Implements: Value Object.
    """

    ACTIVE = "active"
    RETRACTED = "retracted"


REASON_MAX_LENGTH: Final = 1000
NOTE_MAX_LENGTH: Final = 1000

RetractionReason = Annotated[str, *safe_text(REASON_MAX_LENGTH)]
"""Why a claim or damage record was retracted: 1 to 1000 characters of safe text."""

ClaimNote = Annotated[str, *safe_text(NOTE_MAX_LENGTH, allow_line_breaks=True)]
"""A moderator's note on a claim or damage record: 1 to 1000 characters, may span
lines. Never copied into domain events, which carry ids and non-personal fields only.
"""

# --------------------------------------------------------------------------- #
# Sources (mirrored from provenance)                                          #
# --------------------------------------------------------------------------- #

# The domain layer may not import another module (AGENTS.md §2.1), so the names of
# provenance's ``SourceType`` are mirrored here. They must stay equal; the unit tests
# pin the list.
SourceTypeName = Literal[
    "citizen",
    "organisation",
    "government",
    "news",
    "satellite",
    "research",
    "dataset",
]
"""The kind of party or instrument behind a source, as provenance names it."""

SOURCE_TYPE_NAMES: Final[tuple[SourceTypeName, ...]] = get_args(SourceTypeName)


class SourceRank(IntEnum):
    """How much the best-figure policy prefers a source type when claims tie.

    A higher rank wins. The order government > research > organisation > news >
    citizen is the Phase 3 plan's **proposed** default; placing ``satellite`` and
    ``dataset`` between research and organisation is a further proposal (they are
    instrument- or compilation-based, like research, but not peer-reviewed). Ranking
    only breaks ties; it never overrides a newer claim or a larger value.

    Implements: Value Object.
    """

    CITIZEN = 1
    NEWS = 2
    ORGANISATION = 3
    DATASET = 4
    SATELLITE = 5
    RESEARCH = 6
    GOVERNMENT = 7

    @classmethod
    def of(cls, source_type: SourceTypeName) -> Self:
        """Return the rank of a source type.

        Args:
            source_type: One of ``SOURCE_TYPE_NAMES``.

        Returns:
            Its rank.
        """
        return cls[source_type.upper()]


# --------------------------------------------------------------------------- #
# Scope, assets and damage                                                    #
# --------------------------------------------------------------------------- #

# Mirrors geography's ``PLACE_CODE_PATTERN``: the domain may not import geography.
PLACE_CODE_PATTERN: Final = r"^[a-z0-9][a-z0-9_.-]{1,63}$"

PlaceCodeRef = Annotated[str, StringConstraints(pattern=PLACE_CODE_PATTERN)]
"""A geography place code such as ``pk.gb.hunza``, held by reference."""


class ClaimScope(BaseModel):
    """What part of an event a claim covers: a place, an asset, both or all of it.

    Both fields empty means the claim covers the whole event. Two claims of one
    source with equal scopes describe the same thing, so the best-figure policy
    counts only the newer one.

    Implements: Value Object.

    Attributes:
        place_code: The place the figure is about, if narrower than the event.
        asset_id: The infrastructure asset the figure is about, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_code: PlaceCodeRef | None = None
    asset_id: EntityId | None = None

    @property
    def is_whole_event(self) -> bool:
        """Tell whether the claim covers the whole event.

        Returns:
            ``True`` if neither a place nor an asset narrows it.
        """
        return self.place_code is None and self.asset_id is None


class AssetKind(StrEnum):
    """What an infrastructure asset is (proposed list).

    Implements: Value Object.
    """

    BRIDGE = "bridge"
    ROAD_SEGMENT = "road_segment"
    WATER_CHANNEL = "water_channel"
    POWER_LINE = "power_line"
    BUILDING = "building"
    OTHER = "other"


OSM_ID_PATTERN: Final = r"^(node|way|relation)/[1-9][0-9]{0,18}$"

OsmId = Annotated[str, StringConstraints(pattern=OSM_ID_PATTERN)]
"""An OpenStreetMap element reference such as ``way/123456``; ids start at 1."""

ASSET_NAME_MAX_LENGTH: Final = 200

AssetName = Annotated[str, *safe_text(ASSET_NAME_MAX_LENGTH)]
"""An asset's display name, 1 to 200 characters of single-line safe text."""


class DamageLevel(StrEnum):
    """How badly an asset was hit (proposed scale, least to most severe).

    - ``damaged``: still standing, needs repair.
    - ``destroyed``: no longer usable, remains in place.
    - ``washed_away``: carried off by water or debris; nothing usable remains.

    Implements: Value Object.
    """

    DAMAGED = "damaged"
    DESTROYED = "destroyed"
    WASHED_AWAY = "washed_away"
