"""Review marks: how moderators keep track of the reports they have looked at.

Report moderation is **non-blocking and reversible** (maintainer decision,
2026-10-07; ADR 0022). Nothing a reporter sends is refused, rejected or deleted, and
a mark never changes a report's ``ReportStatus`` or what its reporter may do: the
reporter can still revise and withdraw. A mark only says where a report stands in
the moderators' own work:

- ``new``: nobody has marked it yet, or a mark was undone;
- ``reviewed``: a moderator has looked at it (an optional note says what they saw);
- ``archived``: a moderator has set it aside, with a reason (spam, a test, out of
  scope); it stays on record and can be brought back.

**One review per report lineage.** A lineage is a report and all its revisions; its
id is the id of revision 1, which every later revision inherits. A correction is a
new revision of the same observation, so it keeps the lineage's mark instead of
falling back to ``new``; the mark records which revision the moderator looked at
(``ReviewMark.revision``), so a later revision shows as "revised since review".

**Reasons.** Archiving needs a reason; so does every move back to ``new`` and every
move out of ``archived`` (undoing a decision should say why). Marking ``reviewed``
from ``new`` or ``reviewed`` takes an optional note.

**History.** ``ReportReview`` keeps only its current state and the mark that set it
(``last_mark``); every mark is also appended to the review's history by the
repository and never changed or removed afterwards.

A mark that changes nothing (same state, same revision, same reason) returns the
review unchanged and no events, so a retried request is harmless.

Patterns: Entity, Aggregate Root, Value Object, Domain Events.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from yakhnama.modules.reports.domain.errors import ReviewReasonRequiredError
from yakhnama.modules.reports.domain.events import ReportReviewMarked
from yakhnama.modules.reports.domain.value_objects import RevisionNumber
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.text import safe_text

REVIEW_REASON_MAX_LENGTH: Final = 500
ReviewReason = Annotated[
    str, *safe_text(REVIEW_REASON_MAX_LENGTH, allow_line_breaks=True)
]
"""Why a report was marked, 1 to 500 characters of safe text; moderators only."""

REVIEW_VERSION_MAX: Final = 2**31 - 1
ReviewVersion = Annotated[int, Field(ge=1, le=REVIEW_VERSION_MAX)]
"""Optimistic-concurrency version of a review: 1 at the first mark, +1 per mark."""


class ReviewState(StrEnum):
    """Where a report lineage stands in the moderators' work (not its status).

    Implements: Value Object.
    """

    NEW = "new"
    REVIEWED = "reviewed"
    ARCHIVED = "archived"


def is_reason_required(current: ReviewState, target: ReviewState) -> bool:
    """Tell whether moving from ``current`` to ``target`` needs a reason.

    Args:
        current: The lineage's state now (``new`` when it was never marked).
        target: The state asked for.

    Returns:
        ``False`` only for a move to ``reviewed`` from ``new`` or ``reviewed``.
    """
    return target is not ReviewState.REVIEWED or current is ReviewState.ARCHIVED


def require_reason(
    current: ReviewState, target: ReviewState, reason: str | None
) -> None:
    """Refuse a move that needs a reason and has none.

    Args:
        current: The lineage's state now.
        target: The state asked for.
        reason: The reason given, if any.

    Raises:
        ReviewReasonRequiredError: If ``is_reason_required`` and ``reason`` is
            ``None``.
    """
    if reason is None and is_reason_required(current, target):
        raise ReviewReasonRequiredError.for_move(current.value, target.value)


class ReviewMarkRequest(BaseModel):
    """What a moderator asks for when marking one report.

    Implements: Value Object.

    Attributes:
        state: The state asked for.
        reason: Why, or a note; required by ``is_reason_required``.
        report_id: The revision the moderator looked at.
        revision: That revision's number.
        actor_id: The moderator.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ReviewState
    reason: ReviewReason | None = None
    report_id: EntityId
    revision: RevisionNumber
    actor_id: EntityId


class ReviewMark(BaseModel):
    """One mark in a lineage's history; appended once, never changed.

    Implements: Value Object.

    Attributes:
        id: The mark's id (UUIDv7).
        state: The state the mark set.
        reason: Why, or a note, if given.
        report_id: The revision the moderator looked at.
        revision: That revision's number.
        actor_id: The moderator.
        marked_at: When, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    state: ReviewState
    reason: ReviewReason | None
    report_id: EntityId
    revision: RevisionNumber
    actor_id: EntityId
    marked_at: AwareDatetime

    @field_validator("marked_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def repeats(self, request: ReviewMarkRequest) -> bool:
        """Tell whether ``request`` asks for exactly this mark again.

        Args:
            request: The new request.

        Returns:
            ``True`` if state, revision and reason are all equal.
        """
        return (
            self.state is request.state
            and self.report_id == request.report_id
            and self.reason == request.reason
        )


class ReportReview(BaseModel):
    """The moderators' review of one report lineage.

    Invariants: ``state`` is ``last_mark.state``; ``updated_at`` is
    ``last_mark.marked_at``; ``created_at`` is not after ``updated_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: The lineage id: the id of the lineage's revision 1.
        state: The current state.
        last_mark: The mark that set ``state``.
        version: Optimistic-concurrency version.
        created_at: When the lineage was first marked, UTC.
        updated_at: When it was last marked, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    state: ReviewState
    last_mark: ReviewMark
    version: ReviewVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.state is not self.last_mark.state:
            message = "a review's state must be the state of its last mark"
            raise ValueError(message)
        if self.updated_at != self.last_mark.marked_at:
            message = "a review's updated_at must be the time of its last mark"
            raise ValueError(message)
        if self.created_at > self.updated_at:
            message = "created_at must not be later than updated_at"
            raise ValueError(message)
        return self

    @property
    def reviewed_revision(self) -> int:
        """Return the revision the last mark was made on.

        Returns:
            ``last_mark.revision``.
        """
        return self.last_mark.revision

    @property
    def updated_by(self) -> EntityId:
        """Return the moderator who made the last mark.

        Returns:
            ``last_mark.actor_id``.
        """
        return self.last_mark.actor_id

    def mark(
        self, request: ReviewMarkRequest, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["ReportReview"]:
        """Mark the lineage again.

        Args:
            request: The state, reason, revision and moderator.
            clock: Source of the mark's time.
            ids: Source of the mark id and the event id.

        Returns:
            The review with the new mark and ``ReportReviewMarked``, or the review
            unchanged and no events if ``request`` repeats the last mark.

        Raises:
            ReviewReasonRequiredError: If the move needs a reason and has none.
        """
        require_reason(self.state, request.state, request.reason)
        if self.last_mark.repeats(request):
            return AggregateChange[ReportReview](state=self)
        mark = new_mark(request, clock=clock, ids=ids)
        state = ReportReview(
            id=self.id,
            state=mark.state,
            last_mark=mark,
            version=self.version + 1,
            created_at=self.created_at,
            updated_at=mark.marked_at,
        )
        return AggregateChange[ReportReview](
            state=state, events=(marked_event(state, self.state, ids),)
        )


def new_mark(
    request: ReviewMarkRequest, *, clock: Clock, ids: IdGenerator
) -> ReviewMark:
    """Build the mark ``request`` asks for, now.

    Args:
        request: The state, reason, revision and moderator.
        clock: Source of ``marked_at``.
        ids: Source of the mark id.

    Returns:
        The mark.
    """
    return ReviewMark(
        id=ids.new_id(),
        state=request.state,
        reason=request.reason,
        report_id=request.report_id,
        revision=request.revision,
        actor_id=request.actor_id,
        marked_at=clock.now(),
    )


def marked_event(
    review: ReportReview, previous_state: ReviewState, ids: IdGenerator
) -> ReportReviewMarked:
    """Build the event of the mark that produced ``review``.

    The reason is left out: events are relayed and kept in the outbox, and a
    moderator's note may quote the report.

    Args:
        review: The review after the mark.
        previous_state: The state before it (``new`` for a first mark).
        ids: Source of the event id.

    Returns:
        The event.
    """
    mark = review.last_mark
    return ReportReviewMarked(
        event_id=ids.new_id(),
        occurred_at=mark.marked_at,
        aggregate_id=review.id,
        version=review.version,
        state=mark.state.value,
        previous_state=previous_state.value,
        report_id=mark.report_id,
        revision=mark.revision,
        actor_id=mark.actor_id,
        has_reason=mark.reason is not None,
    )
