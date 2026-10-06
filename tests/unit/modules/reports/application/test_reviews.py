"""Unit tests for the review-mark use cases and reads (ADR 0022)."""

from datetime import timedelta
from typing import get_args

import pytest

from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.reports import InMemoryReportQueryService
from tests.unit.modules.reports.application.support import (
    MISSING_ID,
    MODERATOR,
    NOW,
    PUBLIC_COORDINATES,
    REPORTER,
    REPORTER_ID,
    Harness,
    content,
    stored_report,
)
from yakhnama.modules.events.domain.value_objects import ReportLinkRole
from yakhnama.modules.identity.public import Role
from yakhnama.modules.reports.application.commands import (
    MarkReportReview,
    MarkReportReviews,
    ReviseReport,
    WithdrawReport,
)
from yakhnama.modules.reports.application.dto import (
    REVIEW_HISTORY_MAX,
    LinkedEvent,
    LinkRoleName,
    ReportRecord,
    ReviewMarkResult,
    ReviewOutcome,
)
from yakhnama.modules.reports.application.handlers import (
    MarkReportReviewHandler,
    MarkReportReviewsHandler,
)
from yakhnama.modules.reports.application.queries import (
    GetReport,
    GetReportReview,
    ListReports,
)
from yakhnama.modules.reports.application.query_services import (
    AuthorisedReportQueryService,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import (
    ReportFilterForbiddenError,
    ReportNotFoundError,
    ReviewReasonRequiredError,
)
from yakhnama.modules.reports.domain.events import ReportReviewMarked
from yakhnama.modules.reports.domain.reviews import ReviewState
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvariantViolationError,
    PermissionDeniedError,
    PreconditionFailedError,
)
from yakhnama.shared_kernel.ids import EntityId

ADMIN = actor_with({Role.ADMIN}, user_id=SequentialIdGenerator(seed=2301).new_id())
EVENT_IDS = SequentialIdGenerator(seed=2302)
ARCHIVE_REASON = "Test submission by the field team."


def _mark(harness: Harness) -> MarkReportReviewHandler:
    return MarkReportReviewHandler(harness.factory, harness.clock, harness.ids)


def _queries(
    harness: Harness,
) -> tuple[AuthorisedReportQueryService, InMemoryReportQueryService]:
    reads = InMemoryReportQueryService(harness.uow)
    return AuthorisedReportQueryService(reads, PUBLIC_COORDINATES), reads


def _command(
    report_id: EntityId,
    state: ReviewState,
    reason: str | None = None,
    expected_version: int | None = None,
) -> MarkReportReview:
    return MarkReportReview(
        actor=MODERATOR,
        report_id=report_id,
        state=state,
        reason=reason,
        expected_version=expected_version,
    )


async def _revise(harness: Harness, report: Report) -> EntityId:
    detail = await harness.revise()(
        ReviseReport(
            actor=REPORTER,
            report_id=report.id,
            content=content(description="Correction: the flow reached the bridge."),
        )
    )
    return detail.id


# --------------------------------------------------------------------------- #
# MarkReportReview                                                            #
# --------------------------------------------------------------------------- #


async def test_mark_review_by_moderator_records_mark_without_changing_report() -> None:
    report = stored_report()
    harness = Harness(report)

    result = await _mark(harness)(_command(report.id, ReviewState.REVIEWED))

    assert result.outcome is ReviewOutcome.MARKED
    assert (result.lineage_id, result.state, result.version) == (
        report.id,
        ReviewState.REVIEWED,
        1,
    )
    assert harness.uow.reports.committed[report.id] == report
    assert [type(event) for event in harness.uow.committed_events] == [
        ReportReviewMarked
    ]
    assert len(harness.uow.report_reviews.marks[report.id]) == 1


async def test_mark_review_by_admin_is_allowed_because_admin_moderates() -> None:
    report = stored_report()
    harness = Harness(report)

    result = await _mark(harness)(
        MarkReportReview(actor=ADMIN, report_id=report.id, state=ReviewState.REVIEWED)
    )

    assert result.outcome is ReviewOutcome.MARKED


async def test_mark_review_by_reporter_is_refused() -> None:
    report = stored_report()
    harness = Harness(report)

    with pytest.raises(PermissionDeniedError):
        await _mark(harness)(
            MarkReportReview(
                actor=REPORTER, report_id=report.id, state=ReviewState.REVIEWED
            )
        )

    assert harness.uow.report_reviews.committed == {}


async def test_mark_review_of_missing_report_raises_not_found() -> None:
    harness = Harness(stored_report())

    with pytest.raises(ReportNotFoundError):
        await _mark(harness)(_command(MISSING_ID, ReviewState.REVIEWED))


async def test_mark_review_with_stale_expected_version_raises_412() -> None:
    report = stored_report()
    harness = Harness(report)
    await _mark(harness)(_command(report.id, ReviewState.REVIEWED))

    with pytest.raises(PreconditionFailedError):
        await _mark(harness)(
            _command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON, 0)
        )

    assert harness.uow.report_reviews.committed[report.id].state is (
        ReviewState.REVIEWED
    )


async def test_mark_review_with_version_zero_on_unmarked_lineage_succeeds() -> None:
    report = stored_report()
    harness = Harness(report)

    result = await _mark(harness)(
        _command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON, 0)
    )

    assert (result.state, result.version) == (ReviewState.ARCHIVED, 1)


async def test_mark_review_archive_without_reason_raises_and_stores_nothing() -> None:
    report = stored_report()
    harness = Harness(report)

    with pytest.raises(ReviewReasonRequiredError):
        await _mark(harness)(_command(report.id, ReviewState.ARCHIVED))

    assert harness.uow.report_reviews.committed == {}


async def test_mark_review_new_on_unmarked_lineage_is_unchanged() -> None:
    report = stored_report()
    harness = Harness(report)

    result = await _mark(harness)(
        _command(report.id, ReviewState.NEW, "Nothing to undo.")
    )

    assert (result.outcome, result.state, result.version) == (
        ReviewOutcome.UNCHANGED,
        ReviewState.NEW,
        0,
    )
    assert harness.uow.committed_events == ()


async def test_mark_review_repeated_is_unchanged_and_appends_no_mark() -> None:
    report = stored_report()
    harness = Harness(report)
    await _mark(harness)(_command(report.id, ReviewState.REVIEWED))

    result = await _mark(harness)(_command(report.id, ReviewState.REVIEWED))

    assert result.outcome is ReviewOutcome.UNCHANGED
    assert result.version == 1
    assert len(harness.uow.report_reviews.marks[report.id]) == 1


async def test_mark_review_archive_then_unarchive_keeps_full_history() -> None:
    report = stored_report()
    harness = Harness(report)
    mark = _mark(harness)
    await mark(_command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON))

    result = await mark(_command(report.id, ReviewState.NEW, "Confirmed by phone."))

    assert (result.state, result.version) == (ReviewState.NEW, 2)
    states = [mark.state for mark in harness.uow.report_reviews.marks[report.id]]
    assert states == [ReviewState.ARCHIVED, ReviewState.NEW]


async def test_archived_report_can_still_be_revised_and_withdrawn_by_reporter() -> None:
    report = stored_report()
    harness = Harness(report)
    await _mark(harness)(_command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON))

    revision_id = await _revise(harness, report)
    withdrawn = await harness.withdraw()(
        WithdrawReport(actor=REPORTER, report_id=revision_id, reason="Duplicate.")
    )

    assert withdrawn.status is ReportStatus.WITHDRAWN


async def test_mark_review_of_later_revision_uses_the_lineage_of_revision_one() -> None:
    report = stored_report()
    harness = Harness(report)
    revision_id = await _revise(harness, report)

    result = await _mark(harness)(_command(revision_id, ReviewState.REVIEWED))

    assert result.lineage_id == report.id
    review = harness.uow.report_reviews.committed[report.id]
    assert (review.reviewed_revision, review.last_mark.report_id) == (2, revision_id)


# --------------------------------------------------------------------------- #
# Bulk marks                                                                  #
# --------------------------------------------------------------------------- #


async def test_bulk_mark_reports_each_outcome_in_request_order() -> None:
    fresh = stored_report()
    archived = stored_report(id=SequentialIdGenerator(seed=2303).new_id())
    harness = Harness(fresh, archived)
    mark = _mark(harness)
    await mark(_command(archived.id, ReviewState.ARCHIVED, ARCHIVE_REASON))
    await mark(_command(fresh.id, ReviewState.REVIEWED))

    result = await MarkReportReviewsHandler(mark)(
        MarkReportReviews(
            actor=MODERATOR,
            report_ids=(fresh.id, MISSING_ID, archived.id),
            state=ReviewState.REVIEWED,
        )
    )

    assert [(item.report_id, item.outcome) for item in result.items] == [
        (fresh.id, ReviewOutcome.UNCHANGED),
        (MISSING_ID, ReviewOutcome.NOT_FOUND),
        (archived.id, ReviewOutcome.REASON_REQUIRED),
    ]
    assert result.items[1].state is None


async def test_bulk_mark_archives_many_with_one_reason() -> None:
    reports = [
        stored_report(id=SequentialIdGenerator(seed=2310 + index).new_id())
        for index in range(3)
    ]
    harness = Harness(*reports)

    result = await MarkReportReviewsHandler(_mark(harness))(
        MarkReportReviews(
            actor=MODERATOR,
            report_ids=tuple(report.id for report in reports),
            state=ReviewState.ARCHIVED,
            reason="Spam wave.",
        )
    )

    assert {item.outcome for item in result.items} == {ReviewOutcome.MARKED}
    assert {
        review.state for review in harness.uow.report_reviews.committed.values()
    } == {ReviewState.ARCHIVED}


class _ConflictingMarkHandler(MarkReportReviewHandler):
    """A mark handler whose every call loses a race with another moderator."""

    async def __call__(self, command: MarkReportReview) -> ReviewMarkResult:
        del command
        message = "marked concurrently"
        raise ConflictError(message)


async def test_bulk_mark_reports_conflict_for_a_concurrent_mark() -> None:
    report = stored_report()
    harness = Harness(report)
    handler = _ConflictingMarkHandler(harness.factory, harness.clock, harness.ids)

    result = await MarkReportReviewsHandler(handler)(
        MarkReportReviews(
            actor=MODERATOR, report_ids=(report.id,), state=ReviewState.REVIEWED
        )
    )

    assert result.items[0].outcome is ReviewOutcome.CONFLICT


async def test_bulk_mark_by_citizen_is_refused_before_reading() -> None:
    report = stored_report()
    harness = Harness(report)

    with pytest.raises(PermissionDeniedError):
        await MarkReportReviewsHandler(_mark(harness))(
            MarkReportReviews(
                actor=REPORTER, report_ids=(report.id,), state=ReviewState.REVIEWED
            )
        )


def test_bulk_mark_command_refuses_repeated_ids() -> None:
    report = stored_report()

    with pytest.raises(ValueError, match="repeat"):
        MarkReportReviews(
            actor=MODERATOR,
            report_ids=(report.id, report.id),
            state=ReviewState.REVIEWED,
        )


# --------------------------------------------------------------------------- #
# Reads                                                                       #
# --------------------------------------------------------------------------- #


async def test_list_reports_shows_review_to_moderator_and_hides_it_from_reporter() -> (
    None
):
    report = stored_report()
    harness = Harness(report)
    await _mark(harness)(_command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON))
    queries, _ = _queries(harness)

    moderator_page = await queries.list_reports(ListReports(actor=MODERATOR))
    reporter_page = await queries.list_reports(ListReports(actor=REPORTER))

    review = moderator_page.items[0].review
    assert review is not None
    assert (review.state, review.reviewed_revision, review.revised_since) == (
        ReviewState.ARCHIVED,
        1,
        False,
    )
    assert reporter_page.items[0].review is None


async def test_list_reports_review_state_filter_by_citizen_is_forbidden() -> None:
    harness = Harness(stored_report())
    queries, _ = _queries(harness)

    with pytest.raises(ReportFilterForbiddenError):
        await queries.list_reports(
            ListReports(actor=REPORTER, review_state=ReviewState.NEW)
        )


async def test_list_reports_review_state_new_matches_unmarked_lineages() -> None:
    marked = stored_report()
    unmarked = stored_report(id=SequentialIdGenerator(seed=2320).new_id())
    harness = Harness(marked, unmarked)
    await _mark(harness)(_command(marked.id, ReviewState.REVIEWED))
    queries, _ = _queries(harness)

    fresh = await queries.list_reports(
        ListReports(actor=MODERATOR, review_state=ReviewState.NEW)
    )
    reviewed = await queries.list_reports(
        ListReports(actor=MODERATOR, review_state=ReviewState.REVIEWED)
    )

    assert [item.id for item in fresh.items] == [unmarked.id]
    assert [item.id for item in reviewed.items] == [marked.id]


async def test_list_reports_reporter_me_narrows_a_moderators_listing() -> None:
    own = stored_report(
        id=SequentialIdGenerator(seed=2321).new_id(), reporter_id=MODERATOR.user_id
    )
    other = stored_report()
    harness = Harness(own, other)
    queries, _ = _queries(harness)

    everything = await queries.list_reports(ListReports(actor=MODERATOR))
    mine = await queries.list_reports(ListReports(actor=MODERATOR, is_own_only=True))

    assert {item.id for item in everything.items} == {own.id, other.id}
    assert [item.id for item in mine.items] == [own.id]


async def test_list_reports_reporter_me_changes_nothing_for_a_citizen() -> None:
    harness = Harness(stored_report())
    queries, _ = _queries(harness)

    plain = await queries.list_reports(ListReports(actor=REPORTER))
    mine = await queries.list_reports(ListReports(actor=REPORTER, is_own_only=True))

    assert plain == mine


async def test_get_report_after_revision_shows_revised_since_to_moderator() -> None:
    report = stored_report()
    harness = Harness(report)
    await _mark(harness)(_command(report.id, ReviewState.REVIEWED))
    revision_id = await _revise(harness, report)
    queries, reads = _queries(harness)
    link = LinkedEvent(event_id=EVENT_IDS.new_id(), report_id=report.id, role="primary")
    reads.linked_events[report.id] = (link,)

    detail = await queries.get_report(GetReport(actor=MODERATOR, report_id=revision_id))

    assert detail.review is not None
    assert (detail.review.state, detail.review.revised_since) == (
        ReviewState.REVIEWED,
        True,
    )
    assert detail.linked_events == (link,)


async def test_get_report_hides_review_and_links_from_the_reporter() -> None:
    report = stored_report()
    harness = Harness(report)
    await _mark(harness)(_command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON))
    queries, reads = _queries(harness)
    reads.linked_events[report.id] = (
        LinkedEvent(event_id=EVENT_IDS.new_id(), report_id=report.id, role="primary"),
    )

    detail = await queries.get_report(GetReport(actor=REPORTER, report_id=report.id))

    assert detail.review is None
    assert detail.linked_events is None


async def test_get_review_returns_history_newest_first_with_etag_version() -> None:
    report = stored_report()
    harness = Harness(report)
    mark = _mark(harness)
    await mark(_command(report.id, ReviewState.ARCHIVED, ARCHIVE_REASON))
    harness.clock.advance(timedelta(minutes=5))
    await mark(_command(report.id, ReviewState.NEW, "Brought back."))
    queries, _ = _queries(harness)

    review = await queries.get_review(
        GetReportReview(actor=MODERATOR, report_id=report.id)
    )

    assert (review.lineage_id, review.state, review.version) == (
        report.id,
        ReviewState.NEW,
        2,
    )
    assert [entry.state for entry in review.history] == [
        ReviewState.NEW,
        ReviewState.ARCHIVED,
    ]
    assert review.history[1].reason == ARCHIVE_REASON
    assert review.updated_at == NOW + timedelta(minutes=5)
    assert review.is_history_truncated is False


async def test_get_review_of_unmarked_lineage_is_new_at_version_zero() -> None:
    report = stored_report()
    harness = Harness(report)
    queries, _ = _queries(harness)

    review = await queries.get_review(
        GetReportReview(actor=MODERATOR, report_id=report.id)
    )

    assert (review.state, review.version, review.history) == (ReviewState.NEW, 0, ())
    assert review.updated_by is None


async def test_get_review_truncates_history_beyond_the_maximum() -> None:
    report = stored_report()
    harness = Harness(report)
    mark = _mark(harness)
    for index in range(REVIEW_HISTORY_MAX + 1):
        state = ReviewState.ARCHIVED if index % 2 == 0 else ReviewState.NEW
        await mark(_command(report.id, state, f"Round {index}."))
        harness.clock.advance(timedelta(seconds=1))
    queries, _ = _queries(harness)

    review = await queries.get_review(
        GetReportReview(actor=MODERATOR, report_id=report.id)
    )

    assert len(review.history) == REVIEW_HISTORY_MAX
    assert review.is_history_truncated is True
    assert review.history[0].reason == f"Round {REVIEW_HISTORY_MAX}."


async def test_get_review_by_reporter_is_refused() -> None:
    report = stored_report()
    harness = Harness(report)
    queries, _ = _queries(harness)

    with pytest.raises(PermissionDeniedError):
        await queries.get_review(GetReportReview(actor=REPORTER, report_id=report.id))


async def test_get_review_of_missing_report_raises_not_found() -> None:
    harness = Harness(stored_report())
    queries, _ = _queries(harness)

    with pytest.raises(ReportNotFoundError):
        await queries.get_review(GetReportReview(actor=MODERATOR, report_id=MISSING_ID))


class _LineagelessReads(InMemoryReportQueryService):
    """A broken read side that forgets the lineage, to prove it is caught."""

    async def get_report(self, report_id: EntityId) -> ReportRecord | None:
        record = await super().get_report(report_id)
        return (
            None if record is None else record.model_copy(update={"lineage_id": None})
        )


async def test_get_review_refuses_a_record_without_lineage() -> None:
    report = stored_report()
    harness = Harness(report)
    queries = AuthorisedReportQueryService(
        _LineagelessReads(harness.uow), PUBLIC_COORDINATES
    )

    with pytest.raises(InvariantViolationError):
        await queries.get_review(GetReportReview(actor=MODERATOR, report_id=report.id))


async def test_record_of_reporter_keeps_reporter_id() -> None:
    report = stored_report()
    harness = Harness(report)
    _, reads = _queries(harness)

    record = await reads.get_report(report.id)

    assert record is not None
    assert (record.reporter_id, record.lineage_id) == (REPORTER_ID, report.id)


def test_link_role_names_match_the_events_module_roles() -> None:
    assert set(get_args(LinkRoleName)) == set(get_args(ReportLinkRole))
