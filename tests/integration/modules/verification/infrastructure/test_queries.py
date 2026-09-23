"""The SQL verification query service and state read model against real PostGIS.

Listings are compared with the same filters applied in memory to the stored cases,
as the fake query service applies them.
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.identity import ActorTestFactory
from tests.factories.verification import (
    VerificationCaseTestFactory,
    VerificationTargetTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.verification.application.dto import (
    VerificationCaseDetail,
    VerificationCaseSummary,
)
from yakhnama.modules.verification.application.queries import ListVerificationCases
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)
from yakhnama.modules.verification.infrastructure.queries import (
    SqlAlchemyVerificationQueryService,
    SqlAlchemyVerificationStateReadModel,
    decode_created_after,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.pagination import CursorPayload, PageRequest, encode_cursor

pytestmark = pytest.mark.integration

type VerificationFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyVerificationUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
REVIEWER: Final = FACTORY_IDS.new_id()
ASSIGNEE: Final = FACTORY_IDS.new_id()


def _under_review(
    case: VerificationCase, clock: SteppingClock, ids: SequentialIdGenerator
) -> VerificationCase:
    return case.transition(
        VerificationState.UNDER_REVIEW,
        actor_id=REVIEWER,
        reason="Picked up.",
        is_human=True,
        clock=clock,
        ids=ids,
    ).state


@pytest.fixture
async def stored_cases(
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> list[VerificationCase]:
    """Store eight cases over every kind, two states and one assignee."""
    cases: list[VerificationCase] = []
    for index in range(8):
        case = VerificationCaseTestFactory.build(
            target=VerificationTarget(
                kind=list(TargetKind)[index % len(TargetKind)],
                target_id=FACTORY_IDS.new_id(),
            ),
            # Pairs share created_at, so the id breaks ties in the keyset.
            created_at=CREATED + timedelta(minutes=index // 2),
        )
        if index % 2:
            case = _under_review(case, clock, ids)
        if index % 3 == 0:
            case = case.assign(ASSIGNEE, actor_id=REVIEWER, clock=clock, ids=ids).state
        cases.append(case)
    async with verification_uow_factory() as uow:
        for case in cases:
            await uow.verification_cases.add(case)
        await uow.commit()
    return cases


@pytest.fixture
def queries(
    session_factory: async_sessionmaker[AsyncSession],
) -> SqlAlchemyVerificationQueryService:
    """Return the SQL verification query service."""
    return SqlAlchemyVerificationQueryService(session_factory)


def _query(**filters: object) -> ListVerificationCases:
    return ListVerificationCases.model_validate(
        {"actor": ActorTestFactory.build(), "page": PageRequest(limit=100), **filters}
    )


def _expected(
    cases: list[VerificationCase], query: ListVerificationCases
) -> list[UUID]:
    return [
        case.id
        for case in sorted(cases, key=lambda case: (case.created_at, case.id))
        if (query.state is None or case.state is query.state)
        and (query.target_kind is None or case.target.kind is query.target_kind)
        and (query.assigned_to is None or case.assigned_to == query.assigned_to)
    ]


FILTERS: Final[dict[str, dict[str, object]]] = {
    "none": {},
    "state": {"state": VerificationState.UNDER_REVIEW},
    "kind": {"target_kind": TargetKind.EVENT},
    "assignee": {"assigned_to": ASSIGNEE},
    "all": {
        "state": VerificationState.SUBMITTED,
        "target_kind": TargetKind.REPORT,
        "assigned_to": ASSIGNEE,
    },
}


@pytest.mark.parametrize("name", list(FILTERS))
async def test_verification_query_service_list_cases_applies_filters(
    stored_cases: list[VerificationCase],
    queries: SqlAlchemyVerificationQueryService,
    name: str,
) -> None:
    query = _query(**FILTERS[name])

    page = await queries.list_cases(query)

    assert [item.id for item in page.items] == _expected(stored_cases, query)
    assert page.next_cursor is None


async def test_verification_query_service_list_cases_returns_summaries(
    stored_cases: list[VerificationCase],
    queries: SqlAlchemyVerificationQueryService,
) -> None:
    by_id = {case.id: case for case in stored_cases}

    page = await queries.list_cases(_query())

    assert page.items == tuple(
        VerificationCaseSummary.from_entity(by_id[item.id]) for item in page.items
    )


async def test_verification_query_service_list_cases_pages_without_gaps(
    stored_cases: list[VerificationCase],
    queries: SqlAlchemyVerificationQueryService,
) -> None:
    seen: list[UUID] = []
    cursor: str | None = None

    while True:
        page = await queries.list_cases(
            ListVerificationCases(
                actor=ActorTestFactory.build(), page=PageRequest(limit=3, cursor=cursor)
            )
        )
        seen.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == _expected(stored_cases, _query())


async def test_verification_query_service_list_cases_bad_cursor_raises(
    queries: SqlAlchemyVerificationQueryService,
) -> None:
    cursor = encode_cursor(CursorPayload(sort_key="yesterday", last_id=REVIEWER))

    with pytest.raises(ValidationError):
        await queries.list_cases(
            ListVerificationCases(
                actor=ActorTestFactory.build(), page=PageRequest(cursor=cursor)
            )
        )


def test_decode_created_after_naive_instant_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        decode_created_after("2026-09-01T12:00:00")


async def test_verification_query_service_get_and_get_for_target_return_detail(
    stored_cases: list[VerificationCase],
    queries: SqlAlchemyVerificationQueryService,
) -> None:
    case = stored_cases[1]

    by_id = await queries.get(case.id)
    by_target = await queries.get_for_target(case.target)

    assert by_id == VerificationCaseDetail.from_entity(case)
    assert by_target == by_id


async def test_verification_query_service_unknown_case_returns_none(
    stored_cases: list[VerificationCase],
    queries: SqlAlchemyVerificationQueryService,
) -> None:
    del stored_cases

    by_id = await queries.get(FACTORY_IDS.new_id())
    by_target = await queries.get_for_target(VerificationTargetTestFactory.build())

    assert by_id is None
    assert by_target is None


async def test_state_read_model_returns_current_state_or_none(
    stored_cases: list[VerificationCase],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    read_model = SqlAlchemyVerificationStateReadModel(session_factory)
    submitted, under_review = stored_cases[0], stored_cases[1]

    states = [
        await read_model.state_for(submitted.target),
        await read_model.state_for(under_review.target),
        await read_model.state_for(VerificationTargetTestFactory.build()),
    ]

    assert states == [
        VerificationState.SUBMITTED,
        VerificationState.UNDER_REVIEW,
        None,
    ]
