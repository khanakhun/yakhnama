"""Public facade of the ``reports`` module.

Other modules, the API and the composition root import only this file: the
commands, queries, DTOs and handlers, the ports the composition root binds
(including the three answered by other modules), the triage value types an adapter
builds (``PhotoEvidence``, ``ReportSummaryForTriage``) and the task name the worker
routes.

Patterns: Facade.
"""

from yakhnama.modules.reports.application.authorisation import (
    exact_view_policy,
    is_moderator,
    reporter_policy,
    require_user,
    rounded_view_policy,
    submit_policy,
    triage_view_policy,
)
from yakhnama.modules.reports.application.commands import (
    ReviseReport,
    RunTriage,
    SubmitReport,
    WithdrawReport,
)
from yakhnama.modules.reports.application.dto import (
    ReportDetail,
    ReportRecord,
    ReportSummary,
)
from yakhnama.modules.reports.application.handlers import (
    CITIZEN_SOURCE_CITATION,
    CITIZEN_SOURCE_TITLE,
    ORGANISATION_SOURCE_CITATION,
    ORGANISATION_SOURCE_TITLE,
    ReviseReportHandler,
    RunTriageHandler,
    SubmitReportHandler,
    WithdrawReportHandler,
)
from yakhnama.modules.reports.application.ports import (
    RUN_TRIAGE_TASK,
    MediaOwnershipChecker,
    NearbyReportsFinder,
    PhotoEvidenceProvider,
    ReportQueryService,
    ReportRepository,
    ReportsUnitOfWork,
    ReportsUnitOfWorkFactory,
)
from yakhnama.modules.reports.application.queries import (
    FindNearbyReports,
    GetReport,
    ListReports,
)
from yakhnama.modules.reports.application.query_services import (
    AuthorisedReportQueryService,
)
from yakhnama.modules.reports.application.specifications import (
    ReportHazardCodeSpecification,
    ReportInBoundingBoxSpecification,
    ReportObservedFromSpecification,
    ReportObservedToSpecification,
    ReportReporterSpecification,
    ReportStatusSpecification,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import (
    ReportImmutableError,
    ReportNotFoundError,
    ReportRevisionUnchangedError,
    ReportWithdrawnError,
)
from yakhnama.modules.reports.domain.triage import (
    PhotoEvidence,
    ReportSummaryForTriage,
)
from yakhnama.modules.reports.domain.value_objects import (
    HazardGuess,
    ObservationPoint,
    ReportContent,
    ReportStatus,
    TriageFlag,
    TriageResult,
)

__all__ = [
    "CITIZEN_SOURCE_CITATION",
    "CITIZEN_SOURCE_TITLE",
    "ORGANISATION_SOURCE_CITATION",
    "ORGANISATION_SOURCE_TITLE",
    "RUN_TRIAGE_TASK",
    "AuthorisedReportQueryService",
    "FindNearbyReports",
    "GetReport",
    "HazardGuess",
    "ListReports",
    "MediaOwnershipChecker",
    "NearbyReportsFinder",
    "ObservationPoint",
    "PhotoEvidence",
    "PhotoEvidenceProvider",
    "Report",
    "ReportContent",
    "ReportDetail",
    "ReportHazardCodeSpecification",
    "ReportImmutableError",
    "ReportInBoundingBoxSpecification",
    "ReportNotFoundError",
    "ReportObservedFromSpecification",
    "ReportObservedToSpecification",
    "ReportQueryService",
    "ReportRecord",
    "ReportReporterSpecification",
    "ReportRepository",
    "ReportRevisionUnchangedError",
    "ReportStatus",
    "ReportStatusSpecification",
    "ReportSummary",
    "ReportSummaryForTriage",
    "ReportWithdrawnError",
    "ReportsUnitOfWork",
    "ReportsUnitOfWorkFactory",
    "ReviseReport",
    "ReviseReportHandler",
    "RunTriage",
    "RunTriageHandler",
    "SubmitReport",
    "SubmitReportHandler",
    "TriageFlag",
    "TriageResult",
    "WithdrawReport",
    "WithdrawReportHandler",
    "exact_view_policy",
    "is_moderator",
    "reporter_policy",
    "require_user",
    "rounded_view_policy",
    "submit_policy",
    "triage_view_policy",
]
