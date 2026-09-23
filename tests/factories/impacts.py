"""Factories for the ``impacts`` domain: impact metrics.

``ImpactMetricTestFactory`` is suffixed ``TestFactory`` because the domain already has
an ``ImpactMetricFactory`` (``yakhnama.modules.impacts.domain.factories``) that tests
use to exercise creation; this factory builds an ``ImpactMetric`` directly for
arranging state. Codes come from a counter (``test_metric_<n>``), so they never collide
with each other or with a real registry code.

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
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.value_objects import (
    COUNT_UNIT,
    DEFAULT_CURRENCY,
    Aggregation,
    MetricCategory,
    ValueKind,
    default_aggregation,
)
from yakhnama.shared_kernel.value_objects import KNOWN_UNITS

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
