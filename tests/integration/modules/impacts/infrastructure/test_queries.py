"""The SQL impact metric query service against real PostGIS."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.impacts import ImpactMetricTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
)
from yakhnama.modules.impacts.application.queries import ListImpactMetrics
from yakhnama.modules.impacts.application.specifications import (
    ActiveImpactMetricSpecification,
)
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.value_objects import (
    MetricCategory,
    RetirementReason,
)
from yakhnama.modules.impacts.infrastructure.orm import ImpactMetricRow
from yakhnama.modules.impacts.infrastructure.queries import (
    ImpactMetricSpecificationCompiler,
    SqlAlchemyImpactMetricQueryService,
)
from yakhnama.modules.impacts.infrastructure.uow import SqlAlchemyImpactsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import (
    FalseSpecification,
    Specification,
    TrueSpecification,
)

pytestmark = pytest.mark.integration

type ImpactsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyImpactsUnitOfWork]

CATEGORIES = (
    MetricCategory.HUMAN,
    MetricCategory.HUMAN,
    MetricCategory.HOUSING,
    MetricCategory.HUMAN,
    MetricCategory.ECONOMIC,
)


class _UnknownSpecification(Specification[ImpactMetricSummary]):
    """A leaf the SQL compiler has never heard of.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: ImpactMetricSummary) -> bool:
        """Accept everything; only its type matters here.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True


@pytest.fixture
async def stored(
    impacts_uow_factory: ImpactsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> list[ImpactMetric]:
    """Store five metrics (the first human one retired), sorted by code."""
    metrics = [ImpactMetricTestFactory.build(category=item) for item in CATEGORIES]
    metrics[0] = (
        metrics[0]
        .retire(
            RetirementReason(explanation="Test retirement"),
            clock=clock,
            id_generator=ids,
        )
        .state
    )
    async with impacts_uow_factory() as uow:
        for metric in metrics:
            await uow.impact_metrics.add(metric)
        await uow.commit()
    return sorted(metrics, key=lambda metric: metric.code)


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyImpactMetricQueryService:
    """Return the query service on the test database."""
    return SqlAlchemyImpactMetricQueryService(session_factory)


async def _all_pages(
    service: SqlAlchemyImpactMetricQueryService, query: ListImpactMetrics
) -> list[ImpactMetricSummary]:
    items: list[ImpactMetricSummary] = []
    cursor: str | None = None
    while True:
        page = await service.list_impact_metrics(
            ListImpactMetrics(
                category=query.category,
                include_retired=query.include_retired,
                page=PageRequest(limit=query.page.limit, cursor=cursor),
            )
        )
        items.extend(page.items)
        if page.next_cursor is None:
            return items
        cursor = page.next_cursor


async def test_list_impact_metrics_pages_by_code_until_exhausted(
    service: SqlAlchemyImpactMetricQueryService, stored: list[ImpactMetric]
) -> None:
    items = await _all_pages(
        service, ListImpactMetrics(include_retired=True, page=PageRequest(limit=2))
    )

    assert items == [ImpactMetricSummary.from_entity(item) for item in stored]


async def test_list_impact_metrics_by_default_excludes_retired(
    service: SqlAlchemyImpactMetricQueryService, stored: list[ImpactMetric]
) -> None:
    items = await _all_pages(service, ListImpactMetrics(page=PageRequest(limit=2)))

    assert [item.code for item in items] == [
        item.code for item in stored if item.is_active
    ]


async def test_list_impact_metrics_with_category_returns_only_that_category(
    service: SqlAlchemyImpactMetricQueryService, stored: list[ImpactMetric]
) -> None:
    active_human = await _all_pages(
        service,
        ListImpactMetrics(category=MetricCategory.HUMAN, page=PageRequest(limit=1)),
    )
    every_human = await _all_pages(
        service,
        ListImpactMetrics(
            category=MetricCategory.HUMAN,
            include_retired=True,
            page=PageRequest(limit=1),
        ),
    )

    assert [item.code for item in active_human] == [
        item.code
        for item in stored
        if item.category is MetricCategory.HUMAN and item.is_active
    ]
    assert [item.code for item in every_human] == [
        item.code for item in stored if item.category is MetricCategory.HUMAN
    ]


async def test_impact_metric_query_get_returns_detail(
    service: SqlAlchemyImpactMetricQueryService, stored: list[ImpactMetric]
) -> None:
    detail = await service.get(stored[0].code)

    assert detail == ImpactMetricDetail.from_entity(stored[0])


async def test_impact_metric_query_get_when_missing_returns_none(
    service: SqlAlchemyImpactMetricQueryService, stored: list[ImpactMetric]
) -> None:
    detail = await service.get("test_missing")

    assert detail is None


async def test_impact_metric_specification_compiler_combinators_filter_rows(
    session_factory: async_sessionmaker[AsyncSession], stored: list[ImpactMetric]
) -> None:
    compiler = ImpactMetricSpecificationCompiler()
    active: Specification[ImpactMetricSummary] = ActiveImpactMetricSpecification()
    everything: Specification[ImpactMetricSummary] = TrueSpecification()
    nothing: Specification[ImpactMetricSummary] = FalseSpecification()

    async def codes_for(
        specification: Specification[ImpactMetricSummary],
    ) -> set[str]:
        async with session_factory() as session:
            rows = await session.execute(
                select(ImpactMetricRow.code).where(specification.accept(compiler))
            )
            return set(rows.scalars())

    active_codes = await codes_for(active.and_(everything))
    retired_codes = await codes_for(active.not_())
    either = await codes_for(nothing.or_(active))
    none = await codes_for(nothing)

    assert active_codes == either == {item.code for item in stored if item.is_active}
    assert retired_codes == {item.code for item in stored if not item.is_active}
    assert none == set()


def test_impact_metric_specification_compiler_with_unknown_leaf_raises_type_error() -> (
    None
):
    compiler = ImpactMetricSpecificationCompiler()

    with pytest.raises(TypeError, match="_UnknownSpecification"):
        _UnknownSpecification().accept(compiler)


async def test_list_impact_metrics_orders_codes_by_code_point_like_sorted(
    service: SqlAlchemyImpactMetricQueryService, impacts_uow_factory: ImpactsFactory
) -> None:
    # A linguistic collation ignores "_" at the first level and would put
    # "flood_x" after "floodplain"; code-point order puts it first, like sorted().
    codes = ["floodplain", "flood_x", "flood", "flood_a", "floods"]
    async with impacts_uow_factory() as uow:
        for code in codes:
            await uow.impact_metrics.add(ImpactMetricTestFactory.build(code=code))
        await uow.commit()

    paged = await _all_pages(
        service, ListImpactMetrics(include_retired=True, page=PageRequest(limit=2))
    )
    async with impacts_uow_factory() as uow:
        registry = await uow.impact_metrics.list_all()

    assert [item.code for item in paged] == sorted(codes)
    assert list(registry.codes()) == sorted(codes)
