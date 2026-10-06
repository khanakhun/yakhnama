"""Unit tests for ``yakhnama.modules.reports.domain.reviews`` and its factory."""

from datetime import UTC, datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.clock import SteppingClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.reports.domain.errors import (
    ReportFilterForbiddenError,
    ReviewReasonRequiredError,
)
from yakhnama.modules.reports.domain.events import (
    ReportReviewMarked,
    ReviewStateName,
)
from yakhnama.modules.reports.domain.factories import ReportReviewFactory
from yakhnama.modules.reports.domain.reviews import (
    REVIEW_REASON_MAX_LENGTH,
    ReportReview,
    ReviewMark,
    ReviewMarkRequest,
    ReviewState,
    is_reason_required,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError, ValidationError

START = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=2201)
LINEAGE_ID = IDS.new_id()
REVISION_TWO_ID = IDS.new_id()
MODERATOR_ID = IDS.new_id()
OTHER_MODERATOR_ID = IDS.new_id()

NEW = ReviewState.NEW
REVIEWED = ReviewState.REVIEWED
ARCHIVED = ReviewState.ARCHIVED


def _request(
    state: ReviewState,
    reason: str | None = None,
    *,
    report_id: object = LINEAGE_ID,
    revision: int = 1,
    actor_id: object = MODERATOR_ID,
) -> ReviewMarkRequest:
    return ReviewMarkRequest.model_validate(
        {
            "state": state,
            "reason": reason,
            "report_id": report_id,
            "revision": revision,
            "actor_id": actor_id,
        }
    )


def _clock() -> SteppingClock:
    return SteppingClock(START, timedelta(minutes=1))


def _first(state: ReviewState, reason: str | None = None) -> ReportReview:
    change = ReportReviewFactory().first_mark(
        LINEAGE_ID,
        _request(state, reason),
        clock=_clock(),
        ids=SequentialIdGenerator(seed=2202),
    )
    assert change is not None
    return change.state


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (NEW, REVIEWED, False),
        (REVIEWED, REVIEWED, False),
        (ARCHIVED, REVIEWED, True),
        (NEW, ARCHIVED, True),
        (REVIEWED, ARCHIVED, True),
        (ARCHIVED, ARCHIVED, True),
        (NEW, NEW, True),
        (REVIEWED, NEW, True),
        (ARCHIVED, NEW, True),
    ],
)
def test_is_reason_required_follows_the_reversible_rules(
    current: ReviewState, target: ReviewState, *, expected: bool
) -> None:
    assert is_reason_required(current, target) is expected


def test_review_state_name_literal_matches_review_state_enum() -> None:
    assert set(get_args(ReviewStateName)) == {state.value for state in ReviewState}


def test_first_mark_reviewed_without_note_creates_version_one_and_event() -> None:
    change = ReportReviewFactory().first_mark(
        LINEAGE_ID,
        _request(REVIEWED),
        clock=_clock(),
        ids=SequentialIdGenerator(seed=2203),
    )

    assert change is not None
    review = change.state
    assert (review.id, review.state, review.version) == (LINEAGE_ID, REVIEWED, 1)
    assert review.reviewed_revision == 1
    assert review.updated_by == MODERATOR_ID
    assert review.created_at == review.updated_at == START
    (event,) = change.events
    assert isinstance(event, ReportReviewMarked)
    assert (event.state, event.previous_state) == ("reviewed", "new")
    assert (event.aggregate_id, event.aggregate_type) == (LINEAGE_ID, "report_review")
    assert event.has_reason is False


def test_first_mark_archived_without_reason_raises_reason_required() -> None:
    with pytest.raises(ReviewReasonRequiredError) as raised:
        ReportReviewFactory().first_mark(
            LINEAGE_ID,
            _request(ARCHIVED),
            clock=_clock(),
            ids=SequentialIdGenerator(seed=2204),
        )

    assert isinstance(raised.value, ValidationError)
    assert raised.value.details == {
        "reason": "review_reason_required",
        "from_state": "new",
        "to_state": "archived",
    }


def test_first_mark_new_with_reason_returns_none_because_unmarked_is_new() -> None:
    change = ReportReviewFactory().first_mark(
        LINEAGE_ID,
        _request(NEW, "Nothing to undo."),
        clock=_clock(),
        ids=SequentialIdGenerator(seed=2205),
    )

    assert change is None


def test_first_mark_new_without_reason_raises_reason_required() -> None:
    with pytest.raises(ReviewReasonRequiredError):
        ReportReviewFactory().first_mark(
            LINEAGE_ID,
            _request(NEW),
            clock=_clock(),
            ids=SequentialIdGenerator(seed=2206),
        )


def test_mark_repeating_last_mark_returns_review_unchanged_without_events() -> None:
    review = _first(ARCHIVED, "Duplicate of an earlier report.")

    change = review.mark(
        _request(ARCHIVED, "Duplicate of an earlier report."),
        clock=_clock(),
        ids=SequentialIdGenerator(seed=2207),
    )

    assert change.state == review
    assert change.events == ()


def test_mark_archived_to_new_with_reason_bumps_version_and_keeps_created_at() -> None:
    review = _first(ARCHIVED, "Looks like a test.")
    clock = SteppingClock(START + timedelta(hours=1), timedelta(minutes=1))

    change = review.mark(
        _request(NEW, "It was real after all.", actor_id=OTHER_MODERATOR_ID),
        clock=clock,
        ids=SequentialIdGenerator(seed=2208),
    )

    undone = change.state
    assert (undone.state, undone.version) == (NEW, 2)
    assert undone.created_at == review.created_at
    assert undone.updated_at == START + timedelta(hours=1)
    assert undone.updated_by == OTHER_MODERATOR_ID
    (event,) = change.events
    assert isinstance(event, ReportReviewMarked)
    assert (event.state, event.previous_state, event.version) == ("new", "archived", 2)
    assert event.has_reason is True


@pytest.mark.parametrize(
    ("first", "first_reason", "target"),
    [
        (REVIEWED, None, ARCHIVED),
        (ARCHIVED, "Spam.", REVIEWED),
        (REVIEWED, None, NEW),
    ],
)
def test_mark_without_required_reason_raises(
    first: ReviewState, first_reason: str | None, target: ReviewState
) -> None:
    review = _first(first, first_reason)

    with pytest.raises(ReviewReasonRequiredError):
        review.mark(
            _request(target), clock=_clock(), ids=SequentialIdGenerator(seed=2209)
        )


def test_mark_reviewed_again_on_later_revision_records_new_revision() -> None:
    review = _first(REVIEWED)

    change = review.mark(
        _request(REVIEWED, report_id=REVISION_TWO_ID, revision=2),
        clock=SteppingClock(START + timedelta(days=1), timedelta(minutes=1)),
        ids=SequentialIdGenerator(seed=2210),
    )

    assert change.state.reviewed_revision == 2
    assert change.state.last_mark.report_id == REVISION_TWO_ID
    assert change.state.version == 2


def test_mark_event_never_carries_the_reason_text() -> None:
    review = _first(REVIEWED)

    change = review.mark(
        _request(ARCHIVED, "Contains a phone number 0300-0000000."),
        clock=_clock(),
        ids=SequentialIdGenerator(seed=2211),
    )

    (event,) = change.events
    assert "0300" not in event.model_dump_json()


def test_review_with_state_unlike_last_mark_is_refused() -> None:
    review = _first(REVIEWED)

    with pytest.raises(PydanticValidationError, match="state of its last mark"):
        ReportReview.model_validate({**review.model_dump(), "state": ARCHIVED})


def test_review_with_updated_at_unlike_last_mark_is_refused() -> None:
    review = _first(REVIEWED)

    with pytest.raises(PydanticValidationError, match="time of its last mark"):
        ReportReview.model_validate(
            {**review.model_dump(), "updated_at": START + timedelta(hours=2)}
        )


def test_review_created_after_updated_is_refused() -> None:
    review = _first(REVIEWED)

    with pytest.raises(PydanticValidationError, match="created_at"):
        ReportReview.model_validate(
            {**review.model_dump(), "created_at": START + timedelta(hours=2)}
        )


@pytest.mark.parametrize(
    "reason",
    [
        "x" * (REVIEW_REASON_MAX_LENGTH + 1),
        "Spam" + chr(0x202E) + "wave",
        "   ",
    ],
)
def test_review_mark_request_with_unsafe_or_bad_reason_is_refused(reason: str) -> None:
    with pytest.raises(PydanticValidationError):
        _request(ARCHIVED, reason)


def test_review_mark_request_keeps_line_breaks_in_a_note() -> None:
    request = _request(REVIEWED, "Seen.\r\nCompare with the Hunza report.")

    assert request.reason == "Seen.\nCompare with the Hunza report."


def test_review_mark_normalises_marked_at_to_utc() -> None:
    karachi = START.astimezone(timezone(timedelta(hours=5)))

    mark = ReviewMark(
        id=IDS.new_id(),
        state=REVIEWED,
        reason=None,
        report_id=LINEAGE_ID,
        revision=1,
        actor_id=MODERATOR_ID,
        marked_at=karachi,
    )

    assert mark.marked_at.tzinfo is UTC
    assert mark.marked_at == START


def test_report_filter_forbidden_error_names_the_filter() -> None:
    error = ReportFilterForbiddenError.for_filter("review_state")

    assert isinstance(error, PermissionDeniedError)
    assert error.details == {
        "action": "filter reports by review_state",
        "policy": "CanModerate",
    }
