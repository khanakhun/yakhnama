"""Unit tests for ``yakhnama.modules.impacts.domain.registry``."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    ImpactMetricNotFoundError,
    MetricCodeAlreadyUsedError,
)
from yakhnama.modules.impacts.domain.factories import ImpactMetricFactory
from yakhnama.modules.impacts.domain.registry import ImpactMetricRegistry
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    MetricCategory,
    RetirementReason,
    SendaiIndicator,
    ValueKind,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.ids import IdGenerator, Uuid7Generator
from yakhnama.shared_kernel.value_objects import LocalizedText

IDS = Uuid7Generator()
START = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def metrics(
    factory: ImpactMetricFactory, clock: Clock, id_generator: IdGenerator
) -> tuple[ImpactMetric, ...]:
    deaths = factory.create(
        code="deaths",
        labels=LocalizedText(texts={"en": "Deaths"}),
        category=MetricCategory.HUMAN,
        value_kind=ValueKind.COUNT,
        unit="count",
        sendai=SendaiIndicator(code="A-1"),
    ).state
    missing = factory.create(
        code="missing_people",
        labels=LocalizedText(texts={"en": "Missing people"}),
        category=MetricCategory.HUMAN,
        value_kind=ValueKind.COUNT,
        unit="count",
    ).state
    old_area = factory.create(
        code="farmland_area_lost",
        labels=LocalizedText(texts={"en": "Farmland lost"}),
        category=MetricCategory.AGRICULTURE,
        value_kind=ValueKind.MEASUREMENT,
        unit="square_metre",
        sendai=SendaiIndicator(code="C-2"),
    ).state.retire(
        RetirementReason(explanation="test"), clock=clock, id_generator=id_generator
    )
    return deaths, missing, old_area.state


def test_impact_metric_registry_from_metrics_duplicate_code_raises_conflict(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    duplicate = metrics[0].model_copy(update={"id": metrics[1].id})

    with pytest.raises(MetricCodeAlreadyUsedError) as caught:
        ImpactMetricRegistry.from_metrics([*metrics, duplicate])

    assert caught.value.details == {"codes": ["deaths"]}


def test_impact_metric_registry_direct_duplicate_raises_validation_error(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    with pytest.raises(PydanticValidationError, match="duplicate metric codes"):
        ImpactMetricRegistry(metrics=(metrics[0], metrics[0]))


def _metric_with_code(code: str) -> ImpactMetric:
    return ImpactMetric.model_validate(
        {
            "id": IDS.new_id(),
            "code": code,
            "labels": {"texts": {"en": code}},
            "category": "human",
            "value_kind": "count",
            "unit": "count",
            "aggregation": "sum",
            "created_at": START,
            "updated_at": START,
        }
    )


@given(codes=st.lists(st.sampled_from(["deaths", "missing_people", "injured"])))
def test_impact_metric_registry_from_metrics_rejects_exactly_repeated_codes(
    codes: list[str],
) -> None:
    candidates = [_metric_with_code(code) for code in codes]
    is_unique = len(set(codes)) == len(codes)

    try:
        registry = ImpactMetricRegistry.from_metrics(candidates)
    except MetricCodeAlreadyUsedError:
        was_accepted = False
    else:
        was_accepted = registry.codes() == tuple(codes)

    assert was_accepted is is_unique


def test_impact_metric_registry_empty_has_no_codes() -> None:
    registry = ImpactMetricRegistry()

    assert (registry.codes(), registry.active()) == ((), ())


def test_impact_metric_registry_get_known_code_returns_metric(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    metric = registry.get(ImpactMetricRef(code="missing_people"))

    assert metric == metrics[1]


def test_impact_metric_registry_get_retired_code_returns_retired_metric(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    metric = registry.get(ImpactMetricRef(code="farmland_area_lost"))

    assert not metric.is_active


def test_impact_metric_registry_get_unknown_code_raises_not_found(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    with pytest.raises(ImpactMetricNotFoundError) as caught:
        registry.get(ImpactMetricRef(code="injured_people"))

    assert caught.value.details == {"code": "injured_people"}


def test_impact_metric_registry_codes_include_retired_in_order(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    codes = registry.codes()

    assert codes == ("deaths", "missing_people", "farmland_area_lost")


@pytest.mark.parametrize(
    ("code", "expected"),
    [("deaths", True), ("farmland_area_lost", True), ("injured_people", False)],
)
def test_impact_metric_registry_has_code_counts_retired_codes_as_taken(
    metrics: tuple[ImpactMetric, ...], code: str, *, expected: bool
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    result = registry.has_code(code)

    assert result is expected


def test_impact_metric_registry_active_excludes_retired(
    metrics: tuple[ImpactMetric, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    active = registry.active()

    assert active == metrics[:2]


@pytest.mark.parametrize(
    ("category", "expected_codes"),
    [
        (MetricCategory.HUMAN, ("deaths", "missing_people")),
        (MetricCategory.AGRICULTURE, ("farmland_area_lost",)),
        (MetricCategory.SERVICES, ()),
    ],
)
def test_impact_metric_registry_by_category_returns_matching_metrics(
    metrics: tuple[ImpactMetric, ...],
    category: MetricCategory,
    expected_codes: tuple[str, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    found = registry.by_category(category)

    assert tuple(metric.code for metric in found) == expected_codes


@pytest.mark.parametrize(
    ("sendai_code", "expected_codes"),
    [("A-1", ("deaths",)), ("C-2", ("farmland_area_lost",)), ("B-1", ())],
)
def test_impact_metric_registry_by_sendai_returns_mapped_metrics(
    metrics: tuple[ImpactMetric, ...],
    sendai_code: str,
    expected_codes: tuple[str, ...],
) -> None:
    registry = ImpactMetricRegistry.from_metrics(metrics)

    found = registry.by_sendai(sendai_code)

    assert tuple(metric.code for metric in found) == expected_codes
