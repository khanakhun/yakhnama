"""The SQL impact claims query service against real PostGIS.

Listings are compared with the same filters applied in memory to the stored
claims, as the fake query service applies them.
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.identity import ActorTestFactory
from tests.factories.impacts import (
    CLAIM_METRIC_CODE,
    ImpactClaimTestFactory,
    ImpactMetricTestFactory,
    InfrastructureAssetTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.impacts.application.claims_dto import (
    ImpactClaimSummary,
    InfrastructureAssetDetail,
)
from yakhnama.modules.impacts.application.claims_queries import ListClaims
from yakhnama.modules.impacts.domain.claims import ImpactClaim
from yakhnama.modules.impacts.domain.value_objects import ImpactMetricRef, ValueKind
from yakhnama.modules.impacts.infrastructure.claims_queries import (
    SqlAlchemyImpactQueryService,
    decode_created_after,
)
from yakhnama.modules.impacts.infrastructure.claims_uow import (
    SqlAlchemyImpactClaimsUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import CursorPayload, PageRequest, encode_cursor
from yakhnama.shared_kernel.value_objects import Coordinates

pytestmark = pytest.mark.integration

type ClaimsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyImpactClaimsUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
EVENT_ID: Final = FACTORY_IDS.new_id()
OTHER_METRIC: Final = "test_metric_other"


@pytest.fixture
async def stored_claims(
    impact_claims_uow_factory: ClaimsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> list[ImpactClaim]:
    """Store seven claims of one event over two metrics, two retracted."""
    claims: list[ImpactClaim] = []
    for index in range(7):
        claim = ImpactClaimTestFactory.build(
            event_id=EVENT_ID,
            metric=ImpactMetricRef(
                code=CLAIM_METRIC_CODE if index % 2 else OTHER_METRIC
            ),
            # Pairs share created_at, so the id breaks ties in the keyset.
            created_at=CREATED + timedelta(minutes=index // 2),
        )
        if index % 3 == 0:
            claim = claim.retract(
                "Wrong figure.",
                retracted_by=FACTORY_IDS.new_id(),
                clock=clock,
                id_generator=ids,
            ).state
        claims.append(claim)
    async with impact_claims_uow_factory() as uow:
        for code in (CLAIM_METRIC_CODE, OTHER_METRIC):
            await uow.impact_metrics.add(
                ImpactMetricTestFactory.build(code=code, value_kind=ValueKind.COUNT)
            )
        for claim in claims:
            await uow.impact_claims.add(claim)
        # Another event's claim never shows up.
        await uow.impact_claims.add(ImpactClaimTestFactory.build(created_at=CREATED))
        await uow.commit()
    return claims


@pytest.fixture
def queries(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyImpactQueryService:
    """Return the SQL impact query service."""
    return SqlAlchemyImpactQueryService(session_factory)


def _query(
    filters: dict[str, object] | None = None,
    *,
    limit: int = 100,
    cursor: str | None = None,
) -> ListClaims:
    return ListClaims.model_validate(
        {
            "actor": ActorTestFactory.build(),
            "event_id": EVENT_ID,
            "page": PageRequest(limit=limit, cursor=cursor),
            **(filters or {}),
        }
    )


def _expected(claims: list[ImpactClaim], query: ListClaims) -> list[UUID]:
    return [
        claim.id
        for claim in sorted(claims, key=lambda claim: (claim.created_at, claim.id))
        if (query.metric_code is None or claim.metric.code == query.metric_code)
        and (query.include_retracted or claim.is_active)
    ]


FILTERS: Final[dict[str, dict[str, object]]] = {
    "none": {},
    "metric": {"metric_code": CLAIM_METRIC_CODE},
    "active_only": {"include_retracted": False},
    "both": {"metric_code": OTHER_METRIC, "include_retracted": False},
}


@pytest.mark.parametrize("name", list(FILTERS))
async def test_impact_query_service_list_claims_applies_filters(
    stored_claims: list[ImpactClaim],
    queries: SqlAlchemyImpactQueryService,
    name: str,
) -> None:
    query = _query(FILTERS[name])

    page = await queries.list_claims(query)

    expected = _expected(stored_claims, query)
    assert [item.id for item in page.items] == expected
    assert len(expected) > 0
    assert page.next_cursor is None


async def test_impact_query_service_list_claims_returns_public_summaries(
    stored_claims: list[ImpactClaim],
    queries: SqlAlchemyImpactQueryService,
) -> None:
    by_id = {claim.id: claim for claim in stored_claims}

    page = await queries.list_claims(_query())

    assert page.items == tuple(
        ImpactClaimSummary.from_entity(by_id[item.id]) for item in page.items
    )


async def test_impact_query_service_list_claims_pages_without_gaps(
    stored_claims: list[ImpactClaim],
    queries: SqlAlchemyImpactQueryService,
) -> None:
    seen: list[UUID] = []
    cursor: str | None = None

    while True:
        page = await queries.list_claims(_query(limit=2, cursor=cursor))
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == _expected(stored_claims, _query())


async def test_impact_query_service_list_claims_bad_cursor_raises(
    queries: SqlAlchemyImpactQueryService,
) -> None:
    cursor = encode_cursor(CursorPayload(sort_key="soon", last_id=EVENT_ID))

    with pytest.raises(ValidationError):
        await queries.list_claims(_query(cursor=cursor))


def test_decode_created_after_naive_instant_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        decode_created_after("2026-09-01T12:00:00")


async def test_impact_query_service_get_asset_returns_detail_or_none(
    impact_claims_uow_factory: ClaimsFactory,
    queries: SqlAlchemyImpactQueryService,
) -> None:
    asset = InfrastructureAssetTestFactory.build(
        created_at=CREATED,
        osm_id="node/7",
        location=Coordinates(longitude=74.5, latitude=36.25),
    )
    async with impact_claims_uow_factory() as uow:
        await uow.infrastructure_assets.add(asset)
        await uow.commit()

    found = await queries.get_asset(asset.id)
    missing = await queries.get_asset(FACTORY_IDS.new_id())

    assert found == InfrastructureAssetDetail.from_entity(asset)
    assert missing is None
