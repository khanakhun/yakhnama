"""Domain events of the ``reports`` bounded context.

Every event carries the report's id as ``aggregate_id``, ``"report"`` as
``aggregate_type``, and the report's ``version`` and ``revision`` after the change.
**Payloads carry ids, counts, statuses and flag kinds only**: never the description,
the withdrawal reason, the coordinates or the accuracy, because events are relayed to
subscribers and kept in the outbox, and a reporter's words and position are personal
data (``AGENTS.md`` §5, Phase 3 plan §2).

Patterns: Domain Events.
"""

from typing import Annotated, ClassVar, Final, Literal

from pydantic import Field

from yakhnama.modules.reports.domain.value_objects import (
    MEDIA_PER_REPORT_MAX,
    REPORT_AGGREGATE_TYPE,
    TRIAGE_FLAGS_MAX,
    ReportChannel,
    ReportStatus,
    ReportVersion,
    RevisionNumber,
    TriageFlagKind,
)
from yakhnama.shared_kernel.events import DomainEvent
from yakhnama.shared_kernel.ids import EntityId

MediaCount = Annotated[int, Field(ge=0, le=MEDIA_PER_REPORT_MAX)]


class ReportEvent(DomainEvent):
    """Fields shared by every event about a report; never published on its own.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"report"``.
        version: The report's version after the change.
        revision: The report's position in its revision chain.
    """

    aggregate_type: Literal["report"] = REPORT_AGGREGATE_TYPE
    version: ReportVersion
    revision: RevisionNumber


class ReportSubmitted(ReportEvent):
    """A report was submitted as the first revision of an observation.

    Implements: Domain Events.

    Attributes:
        reporter_id: The submitting user, or the guest submission.
        organization_id: The organisation reported for, or ``None``.
        source_id: The provenance record of the report.
        media_count: How many media assets are attached.
        channel: How the report reached the platform; ``account`` for events
            recorded before channels existed.
    """

    event_type: ClassVar[str] = "reports.report_submitted"

    reporter_id: EntityId
    organization_id: EntityId | None
    source_id: EntityId
    media_count: MediaCount
    channel: ReportChannel = ReportChannel.ACCOUNT


class ReportRevised(ReportEvent):
    """A new revision of a report was submitted; ``aggregate_id`` is the new one.

    The previous revision is marked superseded by a separate ``ReportSuperseded``.

    Implements: Domain Events.

    Attributes:
        supersedes_id: The revision this one replaces.
        reporter_id: The submitting user.
        organization_id: The organisation reported for, or ``None``.
        source_id: The provenance record of the report.
        media_count: How many media assets are attached.
        channel: The channel inherited from the corrected report.
    """

    event_type: ClassVar[str] = "reports.report_revised"

    supersedes_id: EntityId
    reporter_id: EntityId
    organization_id: EntityId | None
    source_id: EntityId
    media_count: MediaCount
    channel: ReportChannel = ReportChannel.ACCOUNT


class ReportSuperseded(ReportEvent):
    """A report was replaced by its next revision.

    Implements: Domain Events.

    Attributes:
        superseded_by_id: The revision that replaces it.
    """

    event_type: ClassVar[str] = "reports.report_superseded"

    superseded_by_id: EntityId


class ReportWithdrawn(ReportEvent):
    """A report was taken back by its reporter; the reason stays on the report.

    Implements: Domain Events.

    Attributes:
        previous_status: ``draft`` or ``submitted``.
    """

    event_type: ClassVar[str] = "reports.report_withdrawn"

    previous_status: ReportStatus


class ReportTriaged(ReportEvent):
    """Triage ran over a report and its result was attached; the status is unchanged.

    Implements: Domain Events.

    Attributes:
        flag_kinds: The kind of every flag raised, in rule order.
    """

    event_type: ClassVar[str] = "reports.report_triaged"

    flag_kinds: tuple[TriageFlagKind, ...] = Field(max_length=TRIAGE_FLAGS_MAX)


ReviewStateName = Literal["new", "reviewed", "archived"]
"""The values of ``reviews.ReviewState``, spelled out so this module does not import
``reviews`` (which builds these events); a unit test keeps them equal."""

REPORT_REVIEW_AGGREGATE_TYPE: Final = "report_review"


class ReportReviewMarked(DomainEvent):
    """A moderator marked a report lineage ``new``, ``reviewed`` or ``archived``.

    The report itself does not change (ADR 0022): its status, content and the
    reporter's rights stay as they were. The reason is never carried, only
    whether there was one.

    Implements: Domain Events.

    Attributes:
        aggregate_type: Always ``"report_review"``; ``aggregate_id`` is the
            lineage id.
        version: The review's version after the mark.
        state: The state the mark set.
        previous_state: The state before (``new`` for a first mark).
        report_id: The revision the moderator looked at.
        revision: That revision's number.
        actor_id: The moderator.
        has_reason: Whether a reason or note was given.
    """

    event_type: ClassVar[str] = "reports.report_review_marked"

    aggregate_type: Literal["report_review"] = REPORT_REVIEW_AGGREGATE_TYPE
    version: Annotated[int, Field(ge=1)]
    state: ReviewStateName
    previous_state: ReviewStateName
    report_id: EntityId
    revision: RevisionNumber
    actor_id: EntityId
    has_reason: bool
