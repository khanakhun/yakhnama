"""The SQLAlchemy impact metric repository and unit of work against real PostGIS."""

import pytest

from tests.factories.impacts import ImpactMetricTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.impacts.application.ports import ImpactsUnitOfWorkFactory
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import ImpactMetricNotFoundError
from yakhnama.modules.impacts.domain.registry import ImpactMetricRegistry
from yakhnama.modules.impacts.domain.value_objects import (
    DesInventarField,
    MetricCategory,
    RetirementReason,
    SendaiIndicator,
    ValueKind,
)
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError, InvariantViolationError
from yakhnama.shared_kernel.value_objects import LocalizedText

pytestmark = pytest.mark.integration

type ImpactsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyImpactsUnitOfWork]


def _fully_described_metric() -> ImpactMetric:
    return ImpactMetricTestFactory.build(
        description=LocalizedText(texts={"en": "Test description"}),
        category=MetricCategory.ECONOMIC,
        value_kind=ValueKind.MONETARY,
        sendai=SendaiIndicator(code="C-1"),
        desinventar=DesInventarField(name="Test field"),
    )


async def _store(factory: ImpactsUnitOfWorkFactory, *metrics: ImpactMetric) -> None:
    async with factory() as uow:
        for metric in metrics:
            await uow.impact_metrics.add(metric)
        await uow.commit()


async def _get(factory: ImpactsUnitOfWorkFactory, code: str) -> ImpactMetric | None:
    async with factory() as uow:
        return await uow.impact_metrics.get_by_code(code)


@pytest.mark.parametrize("value_kind", list(ValueKind))
async def test_impact_metric_repository_add_then_get_returns_equal_metric(
    impacts_uow_factory: ImpactsFactory, value_kind: ValueKind
) -> None:
    metric = ImpactMetricTestFactory.build(value_kind=value_kind)

    await _store(impacts_uow_factory, metric)

    assert await _get(impacts_uow_factory, metric.code) == metric


async def test_impact_metric_repository_round_trips_every_optional_field(
    impacts_uow_factory: ImpactsFactory,
) -> None:
    metric = _fully_described_metric()

    await _store(impacts_uow_factory, metric)

    assert await _get(impacts_uow_factory, metric.code) == metric


async def test_impact_metric_repository_get_when_missing_returns_none(
    impacts_uow_factory: ImpactsFactory,
) -> None:
    metric = ImpactMetricTestFactory.build()

    assert await _get(impacts_uow_factory, metric.code) is None


async def test_impact_metric_repository_list_all_rebuilds_registry_by_code(
    impacts_uow_factory: ImpactsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    metrics = [ImpactMetricTestFactory.build() for _ in range(3)]
    retired = (
        metrics[0]
        .retire(
            RetirementReason(
                explanation="Test retirement", replaced_by=metrics[1].code
            ),
            clock=clock,
            id_generator=ids,
        )
        .state
    )
    await _store(impacts_uow_factory, *reversed(metrics))
    async with impacts_uow_factory() as uow:
        await uow.impact_metrics.save(retired)
        await uow.commit()

    async with impacts_uow_factory() as uow:
        registry = await uow.impact_metrics.list_all()

    expected = sorted([retired, *metrics[1:]], key=lambda metric: metric.code)
    assert registry == ImpactMetricRegistry.from_metrics(expected)


async def test_impact_metric_repository_add_duplicate_code_raises_conflict(
    impacts_uow_factory: ImpactsFactory,
) -> None:
    metric = ImpactMetricTestFactory.build()
    await _store(impacts_uow_factory, metric)
    duplicate = ImpactMetricTestFactory.build(code=metric.code)
    other = ImpactMetricTestFactory.build()

    async with impacts_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.impact_metrics.add(duplicate)
        await uow.impact_metrics.add(other)
        await uow.commit()

    assert await _get(impacts_uow_factory, metric.code) == metric
    assert await _get(impacts_uow_factory, other.code) == other


async def test_impact_metric_repository_save_after_list_all_tracks_loaded_version(
    impacts_uow_factory: ImpactsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    metric = ImpactMetricTestFactory.build()
    await _store(impacts_uow_factory, metric)

    async with impacts_uow_factory() as uow:
        registry = await uow.impact_metrics.list_all()
        loaded = registry.get(metric.ref)
        relabelled = loaded.relabel(
            LocalizedText(texts={"en": "Test one"}), clock=clock, id_generator=ids
        ).state
        relabelled = relabelled.relabel(
            LocalizedText(texts={"en": "Test two"}), clock=clock, id_generator=ids
        ).state
        await uow.impact_metrics.save(relabelled)
        await uow.commit()

    assert await _get(impacts_uow_factory, metric.code) == relabelled


async def test_impact_metric_repository_save_with_stale_version_raises_conflict(
    impacts_uow_factory: ImpactsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    metric = ImpactMetricTestFactory.build()
    await _store(impacts_uow_factory, metric)

    async with impacts_uow_factory() as slow:
        stale = await slow.impact_metrics.get_by_code(metric.code)
        async with impacts_uow_factory() as fast:
            fresh = await fast.impact_metrics.get_by_code(metric.code)
            assert fresh is not None
            await fast.impact_metrics.save(
                fresh.relabel(
                    LocalizedText(texts={"en": "Test fast"}),
                    clock=clock,
                    id_generator=ids,
                ).state
            )
            await fast.commit()
        assert stale is not None
        with pytest.raises(ConflictError) as raised:
            await slow.impact_metrics.save(
                stale.relabel(
                    LocalizedText(texts={"en": "Test slow"}),
                    clock=clock,
                    id_generator=ids,
                ).state
            )

    assert raised.value.details["stored"] == metric.version + 1


async def test_impact_metric_repository_save_when_missing_raises_not_found(
    impacts_uow_factory: ImpactsFactory,
) -> None:
    metric = ImpactMetricTestFactory.build()

    async with impacts_uow_factory() as uow:
        with pytest.raises(ImpactMetricNotFoundError):
            await uow.impact_metrics.save(metric)


async def test_impacts_unit_of_work_outside_block_raises_invariant_violation(
    impacts_uow_factory: ImpactsFactory,
) -> None:
    uow = impacts_uow_factory()

    with pytest.raises(InvariantViolationError):
        _ = uow.impact_metrics
