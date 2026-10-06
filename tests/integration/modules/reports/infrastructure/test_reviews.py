"""Report lineages, review marks and linked events against real PostGIS (ADR 0022)."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.factories.base import FACTORY_IDS
from tests.factories.events import EventTestFactory, ReportLinkTestFactory
from tests.factories.reports import ReportContentFactory, ReportTestFactory
from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.events.infrastructure.uow import SqlAlchemyEventsUnitOfWork
from yakhnama.modules.reports.application.dto import LinkedEvent
from yakhnama.modules.reports.application.specifications import (
    ReportReviewStateSpecification,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.factories import ReportReviewFactory
from yakhnama.modules.reports.domain.reviews import (
    ReportReview,
    ReviewMarkRequest,
    ReviewState,
)
from yakhnama.modules.reports.infrastructure.orm import ReportReviewMarkRow, ReportRow
from yakhnama.modules.reports.infrastructure.queries import SqlAlchemyReportQueryService
from yakhnama.modules.reports.infrastructure.uow import SqlAlchemyReportsUnitOfWork
from yakhnama.platform.uow import SqlAlchemyUnitOfWorkFactory
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy

pytestmark = pytest.mark.integration

type ReportsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyReportsUnitOfWork]
type EventsFactory = SqlAlchemyUnitOfWorkFactory[SqlAlchemyEventsUnitOfWork]

CREATED: Final = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
POLICY: Final = PublicCoordinatePolicy(decimals=2)
MODERATOR_ID: Final = FACTORY_IDS.new_id()


def _clock(start: datetime = CREATED) -> SteppingClock:
    return SteppingClock(start + timedelta(hours=1), timedelta(seconds=1))


async def _chain(
    factory: ReportsFactory, ids: SequentialIdGenerator
) -> tuple[Report, Report]:
    """Store a report and its revision 2, the way the revise use case does."""
    original = ReportTestFactory.build(created_at=CREATED)
    clock = _clock()
    revision = original.revise(ReportContentFactory.build(), clock=clock, ids=ids).state
    superseded = original.mark_superseded(revision, clock=clock, ids=ids).state
    async with factory() as uow:
        await uow.reports.add(original)
        await uow.commit()
    async with factory() as uow:
        await uow.reports.add(revision)
        await uow.reports.save(superseded)
        await uow.commit()
    return original, revision


def _request(
    report: Report, state: ReviewState, reason: str | None = None
) -> ReviewMarkRequest:
    return ReviewMarkRequest(
        state=state,
        reason=reason,
        report_id=report.id,
        revision=report.revision,
        actor_id=MODERATOR_ID,
    )


async def _first_mark(
    factory: ReportsFactory,
    report: Report,
    state: ReviewState,
    ids: SequentialIdGenerator,
    reason: str | None = None,
) -> ReportReview:
    change = ReportReviewFactory().first_mark(
        report.id, _request(report, state, reason), clock=_clock(), ids=ids
    )
    assert change is not None
    async with factory() as uow:
        review = change.record_into(uow)
        await uow.report_reviews.add(review)
        await uow.commit()
    return review


# --------------------------------------------------------------------------- #
# Lineage                                                                     #
# --------------------------------------------------------------------------- #


async def test_report_repository_add_sets_lineage_of_revision_one_and_revisions(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    original, revision = await _chain(reports_uow_factory, ids)

    async with reports_uow_factory() as uow:
        lineages = (
            await uow.reports.lineage_of(original.id),
            await uow.reports.lineage_of(revision.id),
            await uow.reports.lineage_of(FACTORY_IDS.new_id()),
        )
    async with session_factory() as session:
        rows = await session.execute(select(ReportRow.id, ReportRow.lineage_id))
        stored = dict(rows.tuples().all())

    assert lineages == (original.id, original.id, None)
    assert stored == {original.id: original.id, revision.id: original.id}


async def test_reports_table_refuses_a_revision_that_is_its_own_lineage(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    _, revision = await _chain(reports_uow_factory, ids)

    async with session_factory() as session:
        with pytest.raises(IntegrityError, match="lineage_root_is_first_revision"):
            await session.execute(
                text("UPDATE reports SET lineage_id = id WHERE id = :id"),
                {"id": revision.id},
            )


# --------------------------------------------------------------------------- #
# Review repository                                                           #
# --------------------------------------------------------------------------- #


async def test_review_repository_add_then_get_round_trips_and_appends_one_mark(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    original, _ = await _chain(reports_uow_factory, ids)

    review = await _first_mark(
        reports_uow_factory, original, ReviewState.ARCHIVED, ids, "Test report."
    )
    async with reports_uow_factory() as uow:
        loaded = await uow.report_reviews.get(original.id)
        missing = await uow.report_reviews.get(FACTORY_IDS.new_id())
    async with session_factory() as session:
        marks = (await session.execute(select(ReportReviewMarkRow))).scalars().all()

    assert loaded == review
    assert missing is None
    assert [(mark.state, mark.reason) for mark in marks] == [
        ("archived", "Test report.")
    ]


async def test_review_repository_save_updates_review_and_keeps_every_mark(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    original, revision = await _chain(reports_uow_factory, ids)
    review = await _first_mark(reports_uow_factory, original, ReviewState.REVIEWED, ids)
    changed = review.mark(
        _request(revision, ReviewState.NEW, "Second look."),
        clock=_clock(CREATED + timedelta(days=1)),
        ids=ids,
    ).state

    async with reports_uow_factory() as uow:
        await uow.report_reviews.save(changed)
        await uow.commit()
    async with reports_uow_factory() as uow:
        loaded = await uow.report_reviews.get(original.id)
    async with session_factory() as session:
        states = (
            await session.execute(
                select(ReportReviewMarkRow.state).order_by(
                    ReportReviewMarkRow.marked_at
                )
            )
        ).scalars()

    assert loaded == changed
    assert list(states) == ["reviewed", "new"]


async def test_review_repository_add_twice_raises_conflict(
    reports_uow_factory: ReportsFactory, ids: SequentialIdGenerator
) -> None:
    original, _ = await _chain(reports_uow_factory, ids)
    review = await _first_mark(reports_uow_factory, original, ReviewState.REVIEWED, ids)

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.report_reviews.add(review)


async def test_review_repository_save_stale_version_raises_conflict(
    reports_uow_factory: ReportsFactory, ids: SequentialIdGenerator
) -> None:
    original, _ = await _chain(reports_uow_factory, ids)
    review = await _first_mark(reports_uow_factory, original, ReviewState.REVIEWED, ids)
    first = review.mark(
        _request(original, ReviewState.ARCHIVED, "One."), clock=_clock(), ids=ids
    ).state
    second = review.mark(
        _request(original, ReviewState.ARCHIVED, "Two."), clock=_clock(), ids=ids
    ).state
    async with reports_uow_factory() as uow:
        await uow.report_reviews.save(first)
        await uow.commit()

    async with reports_uow_factory() as uow:
        with pytest.raises(ConflictError):
            await uow.report_reviews.save(second)


async def test_review_repository_save_without_review_raises_not_found(
    reports_uow_factory: ReportsFactory, ids: SequentialIdGenerator
) -> None:
    original, _ = await _chain(reports_uow_factory, ids)
    change = ReportReviewFactory().first_mark(
        original.id,
        _request(original, ReviewState.REVIEWED),
        clock=_clock(),
        ids=ids,
    )
    assert change is not None
    later = change.state.mark(
        _request(original, ReviewState.ARCHIVED, "Never stored."),
        clock=_clock(),
        ids=ids,
    ).state

    async with reports_uow_factory() as uow:
        with pytest.raises(NotFoundError):
            await uow.report_reviews.save(later)


# --------------------------------------------------------------------------- #
# Query service                                                               #
# --------------------------------------------------------------------------- #


async def test_query_service_joins_review_and_latest_revision(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    original, revision = await _chain(reports_uow_factory, ids)
    review = await _first_mark(reports_uow_factory, original, ReviewState.REVIEWED, ids)
    service = SqlAlchemyReportQueryService(session_factory, POLICY)

    record = await service.get_report(revision.id)
    page = await service.list_reports(
        ReportReviewStateSpecification(ReviewState.REVIEWED), PageRequest()
    )

    assert record is not None
    assert record.lineage_id == original.id
    assert record.review is not None
    assert (record.review.state, record.review.reviewed_revision) == (
        ReviewState.REVIEWED,
        1,
    )
    assert record.review.latest_revision == 2
    assert record.review.version == review.version
    assert {item.id for item in page.items} == {original.id, revision.id}
    assert all(item.review is not None for item in page.items)


async def test_query_service_review_state_new_matches_unmarked_lineages(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    marked, _ = await _chain(reports_uow_factory, ids)
    unmarked = ReportTestFactory.build(created_at=CREATED)
    async with reports_uow_factory() as uow:
        await uow.reports.add(unmarked)
        await uow.commit()
    await _first_mark(reports_uow_factory, marked, ReviewState.ARCHIVED, ids, "Spam.")
    service = SqlAlchemyReportQueryService(session_factory, POLICY)

    fresh = await service.list_reports(
        ReportReviewStateSpecification(ReviewState.NEW), PageRequest()
    )
    not_fresh = await service.list_reports(
        ReportReviewStateSpecification(ReviewState.NEW).not_(), PageRequest()
    )

    assert [item.id for item in fresh.items] == [unmarked.id]
    assert unmarked.id not in {item.id for item in not_fresh.items}
    assert fresh.items[0].review is None


async def test_query_service_lists_marks_newest_first_with_limit(
    reports_uow_factory: ReportsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    original, _ = await _chain(reports_uow_factory, ids)
    review = await _first_mark(reports_uow_factory, original, ReviewState.REVIEWED, ids)
    for index, state in enumerate((ReviewState.ARCHIVED, ReviewState.NEW)):
        review = review.mark(
            _request(original, state, f"Step {index}."),
            clock=_clock(CREATED + timedelta(days=index + 1)),
            ids=ids,
        ).state
        async with reports_uow_factory() as uow:
            await uow.report_reviews.save(review)
            await uow.commit()
    service = SqlAlchemyReportQueryService(session_factory, POLICY)

    marks = await service.list_review_marks(original.id, 2)

    assert [mark.state for mark in marks] == [ReviewState.NEW, ReviewState.ARCHIVED]


async def test_query_service_lists_event_links_of_every_revision(
    reports_uow_factory: ReportsFactory,
    events_uow_factory: EventsFactory,
    session_factory: async_sessionmaker[AsyncSession],
    ids: SequentialIdGenerator,
) -> None:
    original, revision = await _chain(reports_uow_factory, ids)
    unrelated = FACTORY_IDS.new_id()
    first = EventTestFactory.build(
        report_links=(
            ReportLinkTestFactory.build(
                report_id=original.id, role="primary", linked_at=CREATED
            ),
            ReportLinkTestFactory.build(
                report_id=unrelated, role="supporting", linked_at=CREATED
            ),
        )
    )
    second = EventTestFactory.build(
        report_links=(
            ReportLinkTestFactory.build(
                report_id=revision.id,
                role="contradicting",
                linked_at=CREATED + timedelta(hours=1),
            ),
        )
    )
    async with events_uow_factory() as uow:
        await uow.events.add(first)
        await uow.events.add(second)
        await uow.commit()
    service = SqlAlchemyReportQueryService(session_factory, POLICY)

    links = await service.list_linked_events(original.id)

    assert links == (
        LinkedEvent(event_id=first.id, report_id=original.id, role="primary"),
        LinkedEvent(event_id=second.id, report_id=revision.id, role="contradicting"),
    )
