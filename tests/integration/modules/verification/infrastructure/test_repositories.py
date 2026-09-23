"""The SQLAlchemy verification case repository and unit of work against PostGIS."""

from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.verification import (
    VerificationCaseTestFactory,
    VerificationTargetTestFactory,
)
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import (
    VerificationCaseNotFoundError,
)
from yakhnama.modules.verification.domain.value_objects import (
    TargetKind,
    VerificationState,
    VerificationTarget,
)
from yakhnama.modules.verification.infrastructure.uow import (
    SqlAlchemyVerificationUnitOfWork,
)
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError

pytestmark = pytest.mark.integration

type VerificationFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyVerificationUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)
REVIEWER: Final = FACTORY_IDS.new_id()


def _case(target: VerificationTarget | None = None) -> VerificationCase:
    return VerificationCaseTestFactory.build(
        target=target or VerificationTargetTestFactory.build(), created_at=CREATED
    )


def _verified(
    case: VerificationCase, clock: SteppingClock, ids: SequentialIdGenerator
) -> VerificationCase:
    for state in (VerificationState.UNDER_REVIEW, VerificationState.VERIFIED):
        case = case.transition(
            state,
            actor_id=REVIEWER,
            reason="Two sources agree.",
            is_human=True,
            clock=clock,
            ids=ids,
        ).state
    return case


async def _store(factory: VerificationFactory, *cases: VerificationCase) -> None:
    async with factory() as uow:
        for case in cases:
            await uow.verification_cases.add(case)
        await uow.commit()


async def _get(factory: VerificationFactory, case: VerificationCase) -> object:
    async with factory() as uow:
        return await uow.verification_cases.get(case.id)


async def test_case_repository_add_then_get_returns_equal_case_with_history(
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    case = (
        _verified(_case(), clock, ids)
        .assign(FACTORY_IDS.new_id(), actor_id=REVIEWER, clock=clock, ids=ids)
        .state
    )

    await _store(verification_uow_factory, case)

    assert await _get(verification_uow_factory, case) == case


async def test_case_repository_get_for_target_matches_kind_and_id(
    verification_uow_factory: VerificationFactory,
) -> None:
    target_id = FACTORY_IDS.new_id()
    event_case = _case(VerificationTarget(kind=TargetKind.EVENT, target_id=target_id))
    report_case = _case(VerificationTarget(kind=TargetKind.REPORT, target_id=target_id))
    await _store(verification_uow_factory, event_case, report_case)

    async with verification_uow_factory() as uow:
        found = await uow.verification_cases.get_for_target(event_case.target)
        missing = await uow.verification_cases.get_for_target(
            VerificationTarget(kind=TargetKind.CLAIM, target_id=target_id)
        )

    assert found == event_case
    assert missing is None


async def test_case_repository_get_unknown_id_returns_none(
    verification_uow_factory: VerificationFactory,
) -> None:
    async with verification_uow_factory() as uow:
        result = await uow.verification_cases.get(FACTORY_IDS.new_id())

    assert result is None


async def test_case_repository_add_second_case_for_target_raises_conflict(
    verification_uow_factory: VerificationFactory,
) -> None:
    first = _case()
    await _store(verification_uow_factory, first)

    async with verification_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.verification_cases.add(_case(first.target))
        # The savepoint kept the transaction usable.
        assert await uow.verification_cases.get(first.id) == first


async def test_case_repository_add_duplicate_id_raises_conflict(
    verification_uow_factory: VerificationFactory,
) -> None:
    case = _case()
    await _store(verification_uow_factory, case)

    async with verification_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.verification_cases.add(case)


async def test_case_repository_save_after_load_persists_transition(
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    case = _case()
    await _store(verification_uow_factory, case)

    async with verification_uow_factory() as uow:
        loaded = await uow.verification_cases.get(case.id)
        assert isinstance(loaded, VerificationCase)
        moved = _verified(loaded, clock, ids)
        await uow.verification_cases.save(moved)
        await uow.commit()

    assert await _get(verification_uow_factory, case) == moved


async def test_case_repository_save_after_concurrent_change_raises_conflict(
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    case = _case()
    await _store(verification_uow_factory, case)

    async with (
        verification_uow_factory() as first,
        verification_uow_factory() as second,
    ):
        mine = await first.verification_cases.get(case.id)
        theirs = await second.verification_cases.get(case.id)
        assert mine is not None
        assert theirs is not None
        await second.verification_cases.save(_verified(theirs, clock, ids))
        await second.commit()

        with pytest.raises(ConflictError):
            await first.verification_cases.save(_verified(mine, clock, ids))


async def test_case_repository_save_unknown_case_raises_not_found(
    verification_uow_factory: VerificationFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    moved = _verified(_case(), clock, ids)

    async with verification_uow_factory() as uow:
        with pytest.raises(VerificationCaseNotFoundError):
            await uow.verification_cases.save(moved)


async def test_case_repository_row_with_impossible_history_fails_to_load(
    verification_uow_factory: VerificationFactory,
    session_factory: async_sessionmaker[AsyncSession],
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    case = _verified(_case(), clock, ids)
    await _store(verification_uow_factory, case)
    async with session_factory() as session:
        # Out-of-band tampering: the stored state no longer ends the history.
        await session.execute(
            text("UPDATE verification_cases SET state = 'draft' WHERE id = :id"),
            {"id": case.id},
        )
        await session.commit()

    with pytest.raises(ValueError, match="state must be"):
        await _get(verification_uow_factory, case)


async def test_verification_unit_of_work_rollback_discards_added_case(
    verification_uow_factory: VerificationFactory,
) -> None:
    case = _case()

    async with verification_uow_factory() as uow:
        await uow.verification_cases.add(case)
        await uow.rollback()

    assert await _get(verification_uow_factory, case) is None
