"""Unit tests for the impacts queries, specifications, DTOs and read-side fakes."""

import typing

import pydantic
import pytest

from tests.fakes.impacts import (
    InMemoryImpactMetricQueryService,
    InMemoryImpactMetricRepository,
    InMemoryImpactsUnitOfWork,
)
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.unit.modules.impacts.application.support import stored_metric
from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
)
from yakhnama.modules.impacts.application.ports import (
    ImpactMetricQueryService,
    ImpactMetricRepository,
    ImpactsUnitOfWork,
    ImpactsUnitOfWorkFactory,
)
from yakhnama.modules.impacts.application.queries import (
    GetImpactMetric,
    ListImpactMetrics,
)
from yakhnama.modules.impacts.application.specifications import (
    ActiveImpactMetricSpecification,
    ImpactMetricCategorySpecification,
)
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.value_objects import (
    MetricCategory,
    RetirementReason,
)
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import AndSpecification


def three_metrics() -> tuple[ImpactMetric, ...]:
    """Return ``example_a`` and ``example_c`` active, ``example_b`` retired."""
    return (
        stored_metric("example_a"),
        stored_metric("example_b", retirement=RetirementReason(explanation="old")),
        stored_metric("example_c"),
    )


def test_list_impact_metrics_with_limit_above_max_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        ListImpactMetrics(page=PageRequest(limit=201))


def test_list_impact_metrics_by_default_filters_to_active_only() -> None:
    assert isinstance(
        ListImpactMetrics().to_specification(), ActiveImpactMetricSpecification
    )


def test_list_impact_metrics_including_retired_without_category_has_no_filter() -> None:
    assert ListImpactMetrics(include_retired=True).to_specification() is None


def test_list_impact_metrics_with_category_and_active_combines_both() -> None:
    active, retired, _ = (ImpactMetricSummary.from_entity(m) for m in three_metrics())

    specification = ListImpactMetrics(category=MetricCategory.HUMAN).to_specification()

    assert isinstance(specification, AndSpecification)
    assert specification.is_satisfied_by(active) is True
    assert specification.is_satisfied_by(retired) is False


def test_impact_metric_category_specification_rejects_other_category() -> None:
    summary = ImpactMetricSummary.from_entity(stored_metric("example_a"))
    specification = ImpactMetricCategorySpecification(MetricCategory.HOUSING)

    assert specification.category is MetricCategory.HOUSING
    assert specification.is_satisfied_by(summary) is False


def test_get_impact_metric_with_malformed_code_raises_validation_error() -> None:
    with pytest.raises(pydantic.ValidationError):
        GetImpactMetric(code="X")


def test_impact_metric_detail_from_entity_copies_the_definition() -> None:
    _, retired, _ = three_metrics()

    detail = ImpactMetricDetail.from_entity(retired)

    assert detail.code == "example_b"
    assert detail.retirement == RetirementReason(explanation="old")
    assert detail.unit == "count"
    assert detail.aggregation == "sum"
    assert detail.version == retired.version


@pytest.mark.parametrize(
    ("protocol", "fake"),
    [
        (ImpactMetricRepository, InMemoryImpactMetricRepository),
        (ImpactMetricQueryService, InMemoryImpactMetricQueryService),
        (ImpactsUnitOfWork, InMemoryImpactsUnitOfWork),
    ],
)
def test_fake_defines_every_protocol_member(protocol: type, fake: type) -> None:
    members = typing.get_protocol_members(protocol)

    missing = sorted(member for member in members if not hasattr(fake, member))

    # ``impact_metrics`` is an instance attribute of the unit of work fake.
    assert missing in ([], ["impact_metrics"])


def test_fakes_satisfy_ports_statically() -> None:
    uow = InMemoryImpactsUnitOfWork()

    repository: ImpactMetricRepository = uow.impact_metrics
    unit_of_work: ImpactsUnitOfWork = uow
    factory: ImpactsUnitOfWorkFactory = InMemoryUnitOfWorkFactory(uow)
    service: ImpactMetricQueryService = InMemoryImpactMetricQueryService(
        uow.impact_metrics
    )

    assert unit_of_work.impact_metrics is repository
    assert factory() is uow
    assert service is not None


async def test_fake_repository_add_with_taken_code_raises_conflict() -> None:
    existing = stored_metric("example_a")
    repository = InMemoryImpactMetricRepository([existing])

    with pytest.raises(ConflictError):
        await repository.add(existing)


async def test_fake_repository_save_of_unknown_metric_raises_not_found() -> None:
    repository = InMemoryImpactMetricRepository()

    with pytest.raises(NotFoundError):
        await repository.save(stored_metric("example_a"))


async def test_fake_repository_list_all_orders_registry_by_code() -> None:
    first, second, third = three_metrics()
    repository = InMemoryImpactMetricRepository([third, first, second])

    registry = await repository.list_all()

    assert registry.codes() == ("example_a", "example_b", "example_c")


async def test_fake_query_service_pages_by_code_and_filters_retired() -> None:
    service = InMemoryImpactMetricQueryService(
        InMemoryImpactMetricRepository(three_metrics())
    )

    first = await service.list_impact_metrics(
        ListImpactMetrics(include_retired=True, page=PageRequest(limit=2))
    )
    second = await service.list_impact_metrics(
        ListImpactMetrics(
            include_retired=True, page=PageRequest(limit=2, cursor=first.next_cursor)
        )
    )
    active = await service.list_impact_metrics(ListImpactMetrics())

    assert [item.code for item in first.items] == ["example_a", "example_b"]
    assert [item.code for item in second.items] == ["example_c"]
    assert second.next_cursor is None
    assert [item.code for item in active.items] == ["example_a", "example_c"]


async def test_fake_query_service_get_returns_detail_or_none() -> None:
    service = InMemoryImpactMetricQueryService(
        InMemoryImpactMetricRepository(three_metrics())
    )

    found = await service.get("example_a")
    missing = await service.get("example_missing")

    assert found is not None
    assert found.code == "example_a"
    assert missing is None
