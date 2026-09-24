"""Factories for the ``impacts`` domain: metrics, claims, assets and damage records.

``ImpactMetricTestFactory`` is suffixed ``TestFactory`` because the domain already has
an ``ImpactMetricFactory`` (``yakhnama.modules.impacts.domain.factories``) that tests
use to exercise creation; this factory builds an ``ImpactMetric`` directly for
arranging state. Codes come from a counter (``test_metric_<n>``), so they never collide
with each other or with a real registry code. The claim, asset and damage factories
are suffixed the same way for the same reason; claims default to a whole-event
``count`` value of metric ``test_metric_count``.

Patterns: Factory.
"""

from collections.abc import Mapping

from polyfactory import PostGenerated, Use

from tests.factories.base import (
    FACTORY_IDS,
    FACTORY_RANDOM,
    YakhnamaModelFactory,
    pick,
    random_instant,
    sequence,
)
from tests.factories.shared_kernel import LocalizedTextFactory
from yakhnama.modules.impacts.domain.assets import InfrastructureAsset
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.damage import DamageRecord
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.value_objects import (
    COUNT_UNIT,
    DEFAULT_CURRENCY,
    SOURCE_TYPE_NAMES,
    Aggregation,
    AssetKind,
    CountValue,
    DamageLevel,
    ImpactMetricRef,
    MetricCategory,
    ValueKind,
    default_aggregation,
)
from yakhnama.shared_kernel.value_objects import (
    KNOWN_UNITS,
    Confidence,
    DatePrecision,
    DateWithPrecision,
)

CLAIM_METRIC_CODE = "test_metric_count"
"""Metric code of claims built by ``ImpactClaimTestFactory`` unless overridden."""

MEASUREMENT_UNITS: tuple[str, ...] = tuple(sorted(KNOWN_UNITS - {COUNT_UNIT}))
"""Units a ``measurement`` metric may use: every registered unit except ``count``."""


def _value_kind(values: Mapping[str, object]) -> ValueKind:
    value_kind = values["value_kind"]
    if not isinstance(value_kind, ValueKind):
        # A caller may pass the plain string; normalise it rather than reject it.
        return ValueKind(str(value_kind))
    return value_kind


def _unit_for_kind(_name: str, values: Mapping[str, object]) -> str | None:
    # definition_problems fixes the unit per kind: "count" for counts, a registered
    # SI unit other than "count" for measurements, none for money.
    value_kind = _value_kind(values)
    if value_kind is ValueKind.COUNT:
        return COUNT_UNIT
    if value_kind is ValueKind.MEASUREMENT:
        return FACTORY_RANDOM.choice(MEASUREMENT_UNITS)
    return None


def _currency_for_kind(_name: str, values: Mapping[str, object]) -> str | None:
    return DEFAULT_CURRENCY if _value_kind(values) is ValueKind.MONETARY else None


def _aggregation_for_kind(_name: str, values: Mapping[str, object]) -> Aggregation:
    return default_aggregation(_value_kind(values))


def _same_as_created_at(_name: str, values: Mapping[str, object]) -> object:
    # A metric at version 1 has not changed since it was created.
    return values["created_at"]


class ImpactMetricTestFactory(YakhnamaModelFactory[ImpactMetric]):
    """Builds active metrics at version 1 whose unit and currency fit the kind.

    Pass ``value_kind=`` to choose the kind; unit, currency and aggregation follow it
    (the aggregation is the proposed ``default_aggregation``).

    Implements: Factory.
    """

    __model__ = ImpactMetric

    id = Use(FACTORY_IDS.new_id)
    code = sequence("test_metric_{:05d}")
    labels = Use(LocalizedTextFactory.build)
    category = pick(list(MetricCategory))
    value_kind = pick(list(ValueKind))
    unit = PostGenerated(_unit_for_kind)
    currency = PostGenerated(_currency_for_kind)
    aggregation = PostGenerated(_aggregation_for_kind)
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


def _random_count_value() -> CountValue:
    return CountValue(count=FACTORY_RANDOM.randint(0, 1000))


def _random_day() -> DateWithPrecision:
    return DateWithPrecision(value=random_instant(), precision=DatePrecision.DAY)


class ImpactClaimTestFactory(YakhnamaModelFactory[ImpactClaim]):
    """Builds active, whole-event ``count`` claims at version 1.

    Pass ``metric=`` and a matching ``value=`` for other kinds.

    Implements: Factory.
    """

    __model__ = ImpactClaim

    id = Use(FACTORY_IDS.new_id)
    event_id = Use(FACTORY_IDS.new_id)
    metric = Use(ImpactMetricRef, code=CLAIM_METRIC_CODE)
    value = Use(_random_count_value)
    confidence = pick(list(Confidence))
    source_id = Use(FACTORY_IDS.new_id)
    source_type = pick(SOURCE_TYPE_NAMES)
    claimed_at = Use(_random_day)
    recorded_by = Use(FACTORY_IDS.new_id)
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class InfrastructureAssetTestFactory(YakhnamaModelFactory[InfrastructureAsset]):
    """Builds assets at version 1 without OSM id, location or place.

    Implements: Factory.
    """

    __model__ = InfrastructureAsset

    id = Use(FACTORY_IDS.new_id)
    kind = pick(list(AssetKind))
    name = sequence("Test asset {:05d}")
    source_id = Use(FACTORY_IDS.new_id)
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)


class DamageRecordTestFactory(YakhnamaModelFactory[DamageRecord]):
    """Builds active damage records at version 1.

    Implements: Factory.
    """

    __model__ = DamageRecord

    id = Use(FACTORY_IDS.new_id)
    event_id = Use(FACTORY_IDS.new_id)
    asset_id = Use(FACTORY_IDS.new_id)
    level = pick(list(DamageLevel))
    confidence = pick(list(Confidence))
    source_id = Use(FACTORY_IDS.new_id)
    recorded_at = Use(_random_day)
    recorded_by = Use(FACTORY_IDS.new_id)
    created_at = Use(random_instant)
    updated_at = PostGenerated(_same_as_created_at)
