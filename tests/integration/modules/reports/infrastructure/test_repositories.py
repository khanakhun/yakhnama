"""The SQLAlchemy report repository and reports unit of work against real PostGIS."""

from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportContentFactory, ReportTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import ReportNotFoundError
from yakhnama.modules.reports.domain.value_objects import (
    GpsAccuracy,
    HazardGuess,
    ObservationPoint,
    ReportStatus,
    TriageFlag,
    TriageResult,
)
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.value_objects import Confidence, Coordinates

pytestmark = pytest.mark.integration

type ReportsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyReportsUnitOfWork]

# Before the test clock's start, so every change the clock stamps is later.
CREATED: Final = datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC)
# Seventeen significant digits: WKB must keep every bit of the private position.
EXACT_POINT: Final = Coordinates(
    longitude=74.63651234567891, latitude=36.31234567891234
)


def _full_report() -> Report:
    return ReportTestFactory.build(
        created_at=CREATED,
        organization_id=FACTORY_IDS.new_id(),
        observation=ObservationPoint(
            coordinates=EXACT_POINT, accuracy=GpsAccuracy.metres(12.5)
        ),
        original_language="ur",
        hazard_guess=HazardGuess(hazard_code="glof", confidence=Confidence.MEDIUM),
        place_hint="pk.gb.hunza",
        media_ids=(FACTORY_IDS.new_id(), FACTORY_IDS.new_id()),
        triage=TriageResult(
            flags=(
                TriageFlag(
                    kind="duplicate_suspected",
                    detail="a report nearby at the same time",
                    confidence=Confidence.LOW,
                    related_report_id=FACTORY_IDS.new_id(),
                ),
            ),
            evaluated_at=CREATED,
        ),
    )


async def _store(factory: ReportsFactory, *reports: Report) -> None:
    async with factory() as uow:
        for report in reports:
            await uow.reports.add(report)
        await uow.commit()


async def _get(factory: ReportsFactory, report: Report) -> Report | None:
    async with factory() as uow:
        return await uow.reports.get(report.id)


async def test_report_repository_add_then_get_returns_equal_report_with_every_field(
    reports_uow_factory: ReportsFactory,
) -> None:
    report = _full_report()

    await _store(reports_uow_factory, report)
    loaded = await _get(reports_uow_factory, report)

    assert loaded == report


async def test_report_repository_add_draft_without_accuracy_round_trips(
    reports_uow_factory: ReportsFactory,
) -> None:
    report = ReportTestFactory.build(status=ReportStatus.DRAFT, submitted_at=None)

    await _store(reports_uow_factory, report)
    loaded = await _get(reports_uow_factory, report)

    assert loaded == report
    assert loaded is not None
    assert loaded.observation.accuracy is None


async def test_report_repository_get_unknown_id_returns_none(
    reports_uow_factory: ReportsFactory,
) -> None:
    report = ReportTestFactory.build()

    loaded = await _get(reports_uow_factory, report)

    assert loaded is None


async def test_report_repository_add_taken_id_raises_conflict_and_keeps_transaction(
    reports_uow_factory: ReportsFactory,
) -> None:
    report = ReportTestFactory.build()
    other = ReportTestFactory.build()
    await _store(reports_uow_factory, report)

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.reports.add(report)
        await uow.reports.add(other)
        await uow.commit()

    assert await _get(reports_uow_factory, other) == other


async def test_report_repository_revision_and_supersession_in_one_uow_persist_chain(
    reports_uow_factory: ReportsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    original = ReportTestFactory.build(created_at=CREATED)
    await _store(reports_uow_factory, original)
    revision = original.revise(ReportContentFactory.build(), clock=clock, ids=ids).state
    superseded = original.mark_superseded(revision, clock=clock, ids=ids).state

    async with reports_uow_factory() as uow:
        await uow.reports.add(revision)
        await uow.reports.save(superseded)
        await uow.commit()

    assert await _get(reports_uow_factory, revision) == revision
    assert await _get(reports_uow_factory, original) == superseded


async def test_report_repository_second_revision_of_same_report_raises_conflict(
    reports_uow_factory: ReportsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    original = ReportTestFactory.build(created_at=CREATED)
    await _store(reports_uow_factory, original)
    first = original.revise(ReportContentFactory.build(), clock=clock, ids=ids).state
    second = original.revise(ReportContentFactory.build(), clock=clock, ids=ids).state
    await _store(reports_uow_factory, first)

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.reports.add(second)

    assert await _get(reports_uow_factory, second) is None


async def test_report_repository_revision_of_unstored_report_raises_integrity_error(
    reports_uow_factory: ReportsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    original = ReportTestFactory.build(created_at=CREATED)
    revision = original.revise(ReportContentFactory.build(), clock=clock, ids=ids).state

    async with reports_uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.reports.add(revision)


async def test_report_repository_save_withdrawal_persists_reason(
    reports_uow_factory: ReportsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    report = ReportTestFactory.build(created_at=CREATED)
    await _store(reports_uow_factory, report)
    withdrawn = report.withdraw("Test withdrawal reason", clock=clock, ids=ids).state

    async with reports_uow_factory() as uow:
        await uow.reports.save(withdrawn)
        await uow.commit()

    assert await _get(reports_uow_factory, report) == withdrawn


async def test_report_repository_save_stale_version_raises_conflict(
    reports_uow_factory: ReportsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    report = ReportTestFactory.build(created_at=CREATED)
    await _store(reports_uow_factory, report)
    withdrawn = report.withdraw("Test withdrawal reason", clock=clock, ids=ids).state
    async with reports_uow_factory() as uow:
        await uow.reports.save(withdrawn)
        await uow.commit()

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError) as raised:
            await uow.reports.save(withdrawn)

    assert raised.value.details["stored_version"] == withdrawn.version


async def test_report_repository_save_unknown_report_raises_not_found(
    reports_uow_factory: ReportsFactory,
    clock: SteppingClock,
    ids: SequentialIdGenerator,
) -> None:
    report = ReportTestFactory.build(created_at=CREATED)
    withdrawn = report.withdraw("Test withdrawal reason", clock=clock, ids=ids).state

    async with reports_uow_factory() as uow:
        with pytest.raises(ReportNotFoundError):
            await uow.reports.save(withdrawn)


async def test_reports_table_hazard_code_without_confidence_violates_check(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    report = _full_report()
    await _store(reports_uow_factory, report)

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="hazard_guess_complete"):
            await session.execute(
                text("UPDATE reports SET hazard_confidence = NULL WHERE id = :id"),
                {"id": report.id},
            )
