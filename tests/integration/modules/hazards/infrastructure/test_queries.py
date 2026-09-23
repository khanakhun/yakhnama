"""The SQL hazard type query service against real PostGIS."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.hazards import HazardTypeTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.hazards.application.dto import (
    HazardTypeDetail,
    HazardTypeSummary,
)
from yakhnama.modules.hazards.application.queries import ListHazardTypes
from yakhnama.modules.hazards.application.specifications import (
    ActiveHazardTypeSpecification,
)
from yakhnama.modules.hazards.domain.entities import HazardType
from yakhnama.modules.hazards.domain.value_objects import RetirementReason
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
from yakhnama.modules.hazards.infrastructure.queries import (
    HazardTypeSpecificationCompiler,
    SqlAlchemyHazardTypeQueryService,
)
from yakhnama.modules.hazards.infrastructure.uow import SqlAlchemyHazardsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import (
    FalseSpecification,
    Specification,
    TrueSpecification,
)

pytestmark = pytest.mark.integration

type HazardsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyHazardsUnitOfWork]

TYPE_COUNT = 5


class _UnknownSpecification(Specification[HazardTypeSummary]):
    """A leaf the SQL compiler has never heard of.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: HazardTypeSummary) -> bool:
        """Accept everything; only its type matters here.

        Args:
            candidate: Ignored.

        Returns:
            Always ``True``.
        """
        return True


@pytest.fixture
async def stored(
    hazards_uow_factory: HazardsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> list[HazardType]:
    """Store five root hazard types, the second of them retired, sorted by code."""
    hazard_types = [HazardTypeTestFactory.build() for _ in range(TYPE_COUNT)]
    hazard_types[1] = (
        hazard_types[1]
        .retire(RetirementReason(text="Test retirement"), clock=clock, ids=ids)
        .state
    )
    async with hazards_uow_factory() as uow:
        for hazard_type in hazard_types:
            await uow.hazard_types.add(hazard_type)
        await uow.commit()
    return sorted(hazard_types, key=lambda hazard_type: hazard_type.code)


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyHazardTypeQueryService:
    """Return the query service on the test database."""
    return SqlAlchemyHazardTypeQueryService(session_factory)


async def _all_pages(
    service: SqlAlchemyHazardTypeQueryService, *, include_retired: bool, limit: int
) -> list[HazardTypeSummary]:
    items: list[HazardTypeSummary] = []
    cursor: str | None = None
    while True:
        page = await service.list_hazard_types(
            ListHazardTypes(
                include_retired=include_retired,
                page=PageRequest(limit=limit, cursor=cursor),
            )
        )
        items.extend(page.items)
        if page.next_cursor is None:
            return items
        cursor = page.next_cursor


async def test_list_hazard_types_pages_by_code_until_exhausted(
    service: SqlAlchemyHazardTypeQueryService, stored: list[HazardType]
) -> None:
    items = await _all_pages(service, include_retired=True, limit=2)

    assert items == [HazardTypeSummary.from_entity(item) for item in stored]


async def test_list_hazard_types_by_default_excludes_retired(
    service: SqlAlchemyHazardTypeQueryService, stored: list[HazardType]
) -> None:
    items = await _all_pages(service, include_retired=False, limit=3)

    assert [item.code for item in items] == [
        item.code for item in stored if not item.is_retired
    ]


async def test_list_hazard_types_last_full_page_has_no_cursor(
    service: SqlAlchemyHazardTypeQueryService, stored: list[HazardType]
) -> None:
    page = await service.list_hazard_types(
        ListHazardTypes(include_retired=True, page=PageRequest(limit=TYPE_COUNT))
    )

    assert len(page.items) == len(stored)
    assert page.next_cursor is None


async def test_hazard_type_query_get_returns_detail(
    service: SqlAlchemyHazardTypeQueryService, stored: list[HazardType]
) -> None:
    detail = await service.get(stored[0].code)

    assert detail == HazardTypeDetail.from_entity(stored[0])


async def test_hazard_type_query_get_when_missing_returns_none(
    service: SqlAlchemyHazardTypeQueryService, stored: list[HazardType]
) -> None:
    detail = await service.get("test_missing")

    assert detail is None


async def test_hazard_type_specification_compiler_combinators_filter_rows(
    session_factory: async_sessionmaker[AsyncSession], stored: list[HazardType]
) -> None:
    compiler = HazardTypeSpecificationCompiler()
    active: Specification[HazardTypeSummary] = ActiveHazardTypeSpecification()
    everything: Specification[HazardTypeSummary] = TrueSpecification()
    nothing: Specification[HazardTypeSummary] = FalseSpecification()

    async def codes_for(specification: Specification[HazardTypeSummary]) -> set[str]:
        async with session_factory() as session:
            rows = await session.execute(
                select(HazardTypeRow.code).where(specification.accept(compiler))
            )
            return set(rows.scalars())

    active_codes = await codes_for(active.and_(everything))
    retired_codes = await codes_for(active.not_())
    either = await codes_for(nothing.or_(active))
    none = await codes_for(nothing)

    assert (
        active_codes == either == {item.code for item in stored if not item.is_retired}
    )
    assert retired_codes == {item.code for item in stored if item.is_retired}
    assert none == set()


def test_hazard_type_specification_compiler_with_unknown_leaf_raises_type_error() -> (
    None
):
    compiler = HazardTypeSpecificationCompiler()

    with pytest.raises(TypeError, match="_UnknownSpecification"):
        _UnknownSpecification().accept(compiler)


async def test_list_hazard_types_orders_codes_by_code_point_like_sorted(
    service: SqlAlchemyHazardTypeQueryService, hazards_uow_factory: HazardsFactory
) -> None:
    # A linguistic collation ignores "_" at the first level and would put
    # "flood_x" after "floodplain"; code-point order puts it first, like sorted().
    codes = ["floodplain", "flood_x", "flood", "flood_a", "floods"]
    async with hazards_uow_factory() as uow:
        for code in codes:
            await uow.hazard_types.add(HazardTypeTestFactory.build(code=code))
        await uow.commit()

    paged = await _all_pages(service, include_retired=True, limit=2)
    async with hazards_uow_factory() as uow:
        taxonomy = await uow.hazard_types.list_all()

    assert [item.code for item in paged] == sorted(codes)
    assert [item.code for item in taxonomy.hazard_types] == sorted(codes)
