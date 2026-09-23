"""Unit tests for ``tests.factories.impacts``."""

from datetime import UTC

import pytest

from tests.factories.impacts import MEASUREMENT_UNITS, ImpactMetricTestFactory
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.value_objects import (
    COUNT_UNIT,
    DEFAULT_CURRENCY,
    ValueKind,
    default_aggregation,
    definition_problems,
)
from yakhnama.shared_kernel.ids import is_uuid7

BUILDS = 50


def test_impact_metric_test_factory_builds_valid_metrics_with_distinct_ids() -> None:
    metrics = [ImpactMetricTestFactory.build() for _ in range(BUILDS)]

    assert all(
        ImpactMetric.model_validate(metric.model_dump()) == metric for metric in metrics
    )
    assert len({metric.id for metric in metrics}) == BUILDS
    assert len({metric.code for metric in metrics}) == BUILDS
    assert all(is_uuid7(metric.id) for metric in metrics)


def test_impact_metric_test_factory_unit_and_currency_fit_the_kind() -> None:
    metrics = [ImpactMetricTestFactory.build() for _ in range(BUILDS)]

    assert all(
        definition_problems(metric.value_kind, metric.unit, metric.currency) == ()
        for metric in metrics
    )
    assert all(
        metric.aggregation == default_aggregation(metric.value_kind)
        for metric in metrics
    )


def test_impact_metric_test_factory_builds_active_version_one_utc_metrics() -> None:
    metrics = [ImpactMetricTestFactory.build() for _ in range(BUILDS)]

    assert all(metric.is_active and metric.version == 1 for metric in metrics)
    assert all(metric.created_at.tzinfo is UTC for metric in metrics)
    assert all(metric.updated_at == metric.created_at for metric in metrics)


@pytest.mark.parametrize(
    ("value_kind", "expected_units", "expected_currency"),
    [
        (ValueKind.COUNT, {COUNT_UNIT}, None),
        (ValueKind.MEASUREMENT, set(MEASUREMENT_UNITS), None),
        (ValueKind.MONETARY, {None}, DEFAULT_CURRENCY),
    ],
)
def test_impact_metric_test_factory_value_kind_override_drives_unit_and_currency(
    value_kind: ValueKind,
    expected_units: set[str | None],
    expected_currency: str | None,
) -> None:
    metric = ImpactMetricTestFactory.build(value_kind=value_kind)

    assert metric.unit in expected_units
    assert metric.currency == expected_currency


def test_impact_metric_test_factory_accepts_the_value_kind_as_a_string() -> None:
    metric = ImpactMetricTestFactory.build(value_kind="count")

    assert metric.unit == COUNT_UNIT
