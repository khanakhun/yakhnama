"""Domain events of the ``reports`` bounded context.

Every event carries the report's id as ``aggregate_id``, ``"report"`` as
``aggregate_type``, and the report's ``version`` and ``revision`` after the change.
**Payloads carry ids, counts, statuses and flag kinds only**: never the description,
the withdrawal reason, the coordinates or the accuracy, because events are relayed to
subscribers and kept in the outbox, and a reporter's words and position are personal
data (``AGENTS.md`` §5, Phase 3 plan §2).

Patterns: Domain Events.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import Field

from yakhnama.modules.reports.domain.value_objects import (
    MEDIA_PER_REPORT_MAX,
    REPORT_AGGREGATE_TYPE,
    TRIAGE_FLAGS_MAX,
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
        reporter_id: The submitting user.
        organization_id: The organisation reported for, or ``None``.
        source_id: The provenance record of the report.
        media_count: How many media assets are attached.
    """

    event_type: ClassVar[str] = "reports.report_submitted"

    reporter_id: EntityId
    organization_id: EntityId | None
    source_id: EntityId
    media_count: MediaCount


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
    """

    event_type: ClassVar[str] = "reports.report_revised"

    supersedes_id: EntityId
    reporter_id: EntityId
    organization_id: EntityId | None
    source_id: EntityId
    media_count: MediaCount


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
