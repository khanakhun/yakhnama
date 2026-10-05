"""Report channels, guest submissions and spent challenges against real PostGIS."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import FrozenClock
from yakhnama.modules.reports.application.specifications import (
    ReportChannelSpecification,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import GuestChallengeSpentError
from yakhnama.modules.reports.domain.guest_submissions import (
    GuestSubmission,
    GuestSubmissionLimits,
)
from yakhnama.modules.reports.domain.value_objects import (
    AssistedSubmission,
    ConsentMethod,
    ReportChannel,
)
from yakhnama.modules.reports.infrastructure.queries import (
    SqlAlchemyReportQueryService,
)
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy

pytestmark = pytest.mark.integration

type ReportsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyReportsUnitOfWork]

OPENED: Final = datetime(2026, 10, 5, 12, 0, 0, 250000, tzinfo=UTC)
ASSISTED: Final = AssistedSubmission(
    consent_method=ConsentMethod.WRITTEN,
    consent_statement_version="2026-10-05",
    note="First line.\nSecond line.",
)


def _submission(clock: FrozenClock | None = None) -> GuestSubmission:
    return GuestSubmission.open(
        FACTORY_IDS.new_id(),
        "k" * 43,
        limits=GuestSubmissionLimits(),
        clock=clock or FrozenClock(OPENED),
    ).state


async def _store_report(factory: ReportsFactory, report: Report) -> None:
    async with factory() as uow:
        await uow.reports.add(report)
        await uow.commit()


async def test_assisted_report_round_trips_channel_and_consent_record(
    reports_uow_factory: ReportsFactory,
) -> None:
    report = ReportTestFactory.build(channel=ReportChannel.ASSISTED, assisted=ASSISTED)

    await _store_report(reports_uow_factory, report)
    async with reports_uow_factory() as uow:
        loaded = await uow.reports.get(report.id)

    assert loaded == report


async def test_reports_table_refuses_consent_record_on_an_account_report(
    session_factory: async_sessionmaker[AsyncSession],
    reports_uow_factory: ReportsFactory,
) -> None:
    report = ReportTestFactory.build()
    await _store_report(reports_uow_factory, report)

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="assisted_matches_channel"):
            await session.execute(
                text(
                    "UPDATE reports SET assisted_consent_method = 'verbal', "
                    "assisted_consent_statement_version = 'v1' WHERE id = :id"
                ),
                {"id": report.id},
            )


async def test_query_service_filters_by_channel_and_reads_it_back(
    session_factory: async_sessionmaker[AsyncSession],
    reports_uow_factory: ReportsFactory,
) -> None:
    guest = ReportTestFactory.build(channel=ReportChannel.GUEST)
    account = ReportTestFactory.build()
    for report in (guest, account):
        await _store_report(reports_uow_factory, report)
    service = SqlAlchemyReportQueryService(
        session_factory, PublicCoordinatePolicy(decimals=2)
    )

    page = await service.list_reports(
        ReportChannelSpecification(ReportChannel.GUEST), PageRequest()
    )
    record = await service.get_report(guest.id)

    assert [item.id for item in page.items] == [guest.id]
    assert page.items[0].channel is ReportChannel.GUEST
    assert record is not None
    assert record.channel is ReportChannel.GUEST


async def test_guest_submission_add_save_get_round_trips_every_field(
    reports_uow_factory: ReportsFactory,
) -> None:
    clock = FrozenClock(OPENED)
    submission = _submission(clock)
    report = ReportTestFactory.build(
        reporter_id=submission.id, channel=ReportChannel.GUEST
    )
    await _store_report(reports_uow_factory, report)
    async with reports_uow_factory() as uow:
        await uow.guest_submissions.add(submission)
        await uow.commit()
    clock.advance(timedelta(minutes=1))
    with_media = submission.attach_media(FACTORY_IDS.new_id(), clock=clock).state
    closed = with_media.record_report(
        report.id, "YK-ABCD-2345", "e" * 64, clock=clock
    ).state

    async with reports_uow_factory() as uow:
        await uow.guest_submissions.save(with_media)
        await uow.guest_submissions.save(closed)
        await uow.commit()
    async with reports_uow_factory() as uow:
        loaded = await uow.guest_submissions.get(submission.id)
        is_taken = await uow.guest_submissions.is_reference_taken("YK-ABCD-2345")
        is_free = await uow.guest_submissions.is_reference_taken("YK-ABCD-2346")
        missing = await uow.guest_submissions.get(FACTORY_IDS.new_id())

    assert loaded == closed
    assert (is_taken, is_free) == (True, False)
    assert missing is None


async def test_guest_submission_stale_save_is_a_conflict(
    reports_uow_factory: ReportsFactory,
) -> None:
    clock = FrozenClock(OPENED)
    submission = _submission(clock)
    async with reports_uow_factory() as uow:
        await uow.guest_submissions.add(submission)
        await uow.commit()
    first = submission.attach_media(FACTORY_IDS.new_id(), clock=clock).state
    second = submission.attach_media(FACTORY_IDS.new_id(), clock=clock).state
    async with reports_uow_factory() as uow:
        await uow.guest_submissions.save(first)
        await uow.commit()

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.guest_submissions.save(second)


async def test_guest_submission_save_of_unknown_or_duplicate_add_fails(
    reports_uow_factory: ReportsFactory,
) -> None:
    clock = FrozenClock(OPENED)
    submission = _submission(clock)
    async with reports_uow_factory() as uow:
        with pytest.raises(NotFoundError):
            await uow.guest_submissions.save(
                submission.attach_media(FACTORY_IDS.new_id(), clock=clock).state
            )
        await uow.guest_submissions.add(submission)
        with pytest.raises(ConflictError):
            await uow.guest_submissions.add(submission)


async def test_guest_submission_reference_used_twice_is_a_conflict(
    reports_uow_factory: ReportsFactory,
) -> None:
    clock = FrozenClock(OPENED)
    first, second = _submission(clock), _submission(clock)
    reports = [
        ReportTestFactory.build(reporter_id=item.id, channel=ReportChannel.GUEST)
        for item in (first, second)
    ]
    for report in reports:
        await _store_report(reports_uow_factory, report)
    async with reports_uow_factory() as uow:
        await uow.guest_submissions.add(first)
        await uow.guest_submissions.add(second)
        await uow.guest_submissions.save(
            first.record_report(
                reports[0].id, "YK-SAME-2222", "a" * 64, clock=clock
            ).state
        )
        await uow.commit()

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.guest_submissions.save(
                second.record_report(
                    reports[1].id, "YK-SAME-2222", "b" * 64, clock=clock
                ).state
            )


async def test_count_opened_since_counts_the_window_and_its_oldest(
    reports_uow_factory: ReportsFactory,
) -> None:
    old = _submission(FrozenClock(OPENED - timedelta(hours=2)))
    recent = [
        _submission(FrozenClock(OPENED + timedelta(minutes=minutes)))
        for minutes in (5, 10)
    ]
    async with reports_uow_factory() as uow:
        for submission in (old, *recent):
            await uow.guest_submissions.add(submission)
        await uow.commit()

    async with reports_uow_factory() as uow:
        window = await uow.guest_submissions.count_opened_since(OPENED)
        empty = await uow.guest_submissions.count_opened_since(
            OPENED + timedelta(days=1)
        )

    assert window.count == 2
    assert window.oldest_opened_at == recent[0].created_at
    assert (empty.count, empty.oldest_opened_at) == (0, None)


async def test_spent_challenge_twice_is_refused_until_purged(
    reports_uow_factory: ReportsFactory,
) -> None:
    expires = OPENED + timedelta(minutes=10)
    async with reports_uow_factory() as uow:
        await uow.spent_challenges.spend("salt-one-000000000000", expires)
        await uow.commit()

    async with reports_uow_factory() as uow:
        with pytest.raises(GuestChallengeSpentError):
            await uow.spent_challenges.spend("salt-one-000000000000", expires)
    async with reports_uow_factory() as uow:
        kept = await uow.spent_challenges.purge_expired(expires)
        purged = await uow.spent_challenges.purge_expired(
            expires + timedelta(seconds=1)
        )
        await uow.spent_challenges.spend("salt-one-000000000000", expires)
        await uow.commit()

    assert (kept, purged) == (0, 1)
