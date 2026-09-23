"""Creation of reports.

A report's id comes from the reporting client (``ClientReportId``), not from the
platform's ``IdGenerator``: an offline client names the report when the observation is
written down, so a retried submission carries the same id and ``SubmitReport`` can be
idempotent on it. Checking that no report with the id exists yet needs a repository
and belongs to the command handler.

Patterns: Factory.
"""

from datetime import datetime

from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.events import ReportSubmitted
from yakhnama.modules.reports.domain.value_objects import (
    ClientReportId,
    ReportAttribution,
    ReportContent,
    ReportStatus,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import IdGenerator


class ReportFactory:
    """Create draft or submitted reports at revision 1 and version 1.

    Implements: Factory.
    """

    def draft(
        self,
        report_id: ClientReportId,
        attribution: ReportAttribution,
        content: ReportContent,
        *,
        clock: Clock,
    ) -> AggregateChange[Report]:
        """Create a draft; nothing is published until it is submitted.

        Args:
            report_id: The client-generated UUIDv7.
            attribution: Reporter, organisation and source.
            content: What the reporter stated.
            clock: Source of ``created_at`` and ``updated_at``.

        Returns:
            The draft and no events: a draft is private to its reporter.

        Raises:
            pydantic.ValidationError: If ``report_id`` is not a UUIDv7.
        """
        now = clock.now()
        report = _build(report_id, attribution, content, created_at=now)
        return AggregateChange[Report](state=report)

    def submitted(
        self,
        report_id: ClientReportId,
        attribution: ReportAttribution,
        content: ReportContent,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange[Report]:
        """Create a report that is submitted at once, as ``SubmitReport`` does.

        Args:
            report_id: The client-generated UUIDv7.
            attribution: Reporter, organisation and source.
            content: What the reporter stated.
            clock: Source of every timestamp.
            ids: Source of the event id.

        Returns:
            The submitted report and ``ReportSubmitted``, with the payload a draft
            gets from ``Report.submit`` except ``version`` (1 here, 2 there).

        Raises:
            pydantic.ValidationError: If ``report_id`` is not a UUIDv7.
        """
        now = clock.now()
        report = _build(
            report_id,
            attribution,
            content,
            created_at=now,
            status=ReportStatus.SUBMITTED,
            submitted_at=now,
        )
        event = ReportSubmitted(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=report.id,
            version=report.version,
            revision=report.revision,
            reporter_id=report.reporter_id,
            organization_id=report.organization_id,
            source_id=report.source_id,
            media_count=len(report.media_ids),
        )
        return AggregateChange[Report](state=report, events=(event,))


def _build(
    report_id: ClientReportId,
    attribution: ReportAttribution,
    content: ReportContent,
    *,
    created_at: datetime,
    **fields: object,
) -> Report:
    return Report.model_validate(
        {
            **content.model_dump(),
            **attribution.model_dump(),
            "id": report_id,
            "created_at": created_at,
            "updated_at": created_at,
            **fields,
        }
    )
