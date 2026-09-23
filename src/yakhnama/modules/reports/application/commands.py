"""Write requests accepted by the reports command handlers.

Commands acting on an existing report accept an optional ``expected_version``: the
API fills it from ``If-Match`` and the handler raises ``PreconditionFailedError``
(HTTP 412) when the stored version differs.

Patterns: Command.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.reports.domain.value_objects import (
    ClientReportId,
    ReportContent,
    ReportVersion,
    WithdrawalReason,
)
from yakhnama.shared_kernel.ids import EntityId


class SubmitReport(BaseModel):
    """Submit a new report; idempotent on ``client_report_id``.

    Implements: Command.

    Attributes:
        actor: The reporter.
        client_report_id: The UUIDv7 the reporting client generated; it becomes the
            report's id, so a retried submission is recognised.
        content: What the reporter observed.
        organization_id: The organisation the reporter reports for, if any; the
            reporter must belong to it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    client_report_id: ClientReportId
    content: ReportContent
    organization_id: EntityId | None = None


class ReviseReport(BaseModel):
    """Correct a submitted report by submitting its next revision.

    Implements: Command.

    Attributes:
        actor: The reporter.
        report_id: The current revision being corrected.
        content: The complete corrected content.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_id: EntityId
    content: ReportContent
    expected_version: ReportVersion | None = None


class WithdrawReport(BaseModel):
    """Take a report back; it stays on record with the reason.

    Implements: Command.

    Attributes:
        actor: The reporter.
        report_id: The report.
        reason: Why.
        expected_version: The version the client last saw, from ``If-Match``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_id: EntityId
    reason: WithdrawalReason
    expected_version: ReportVersion | None = None


class RunTriage(BaseModel):
    """Run the triage chain over one report and store its suggestions.

    Internal: enqueued as the ``reports.run_triage`` task after a submission or a
    revision; no endpoint accepts it and it carries no actor (see
    ``RunTriageHandler``).

    Implements: Command.

    Attributes:
        report_id: The report to triage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: EntityId
