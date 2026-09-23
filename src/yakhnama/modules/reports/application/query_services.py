"""Authorised read use cases of the reports module, with the privacy rules applied.

``AuthorisedReportQueryService`` asks the policies in ``authorisation`` about the
query's actor, reads internal ``ReportRecord`` values through the
``ReportQueryService`` port and returns only ``ReportSummary`` and ``ReportDetail``
built with the right precision (see ``dto``). A report the actor may not read is
reported as missing, so its existence is not revealed.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.reports.application.authorisation import (
    exact_view_policy,
    is_moderator,
    require_user,
    rounded_view_policy,
    triage_view_policy,
)
from yakhnama.modules.reports.application.dto import ReportDetail, ReportSummary
from yakhnama.modules.reports.application.ports import ReportQueryService
from yakhnama.modules.reports.application.queries import GetReport, ListReports
from yakhnama.modules.reports.application.specifications import (
    ReportReporterSpecification,
)
from yakhnama.modules.reports.domain.errors import ReportNotFoundError
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy


class AuthorisedReportQueryService:
    """Answer report queries with authorisation and coordinate rounding.

    Implements: Query Service.
    """

    def __init__(
        self,
        query_service: ReportQueryService,
        public_coordinates: PublicCoordinatePolicy,
    ) -> None:
        """Create the service.

        Args:
            query_service: The read port.
            public_coordinates: The rounding for every non-exact view, built from
                ``Settings.public_coordinate_decimals``.
        """
        self._query_service = query_service
        self._public_coordinates = public_coordinates

    async def get_report(self, query: GetReport) -> ReportDetail:
        """Return one report as the actor may see it.

        Args:
            query: The actor and the report id.

        Returns:
            The exact view for the reporter and moderators, the rounded view for
            members of the report's organisation.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            ReportNotFoundError: If the report does not exist or the actor may not
                read it.
        """
        require_user(query.actor, action="read reports")
        record = await self._query_service.get_report(query.report_id)
        if record is None:
            raise ReportNotFoundError.for_id(query.report_id)
        is_triage_visible = triage_view_policy().is_allowed(query.actor)
        if exact_view_policy(record).is_allowed(query.actor):
            return ReportDetail.from_record(
                record, public_coordinates=None, is_triage_visible=is_triage_visible
            )
        rounded = rounded_view_policy(record)
        if rounded is not None and rounded.is_allowed(query.actor):
            return ReportDetail.from_record(
                record,
                public_coordinates=self._public_coordinates,
                is_triage_visible=False,
            )
        raise ReportNotFoundError.for_id(query.report_id)

    async def list_reports(self, query: ListReports) -> Page[ReportSummary]:
        """Return one page of reports, newest first, with rounded positions.

        Args:
            query: The actor, filters and page request.

        Returns:
            The page; for a non-moderator only their own reports.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            ValidationError: If the cursor is invalid.
        """
        user_id = require_user(query.actor, action="list reports")
        specification = query.to_specification(self._public_coordinates)
        if not is_moderator(query.actor):
            specification = specification.and_(ReportReporterSpecification(user_id))
        page = await self._query_service.list_reports(specification, query.page)
        return Page[ReportSummary](
            items=tuple(
                ReportSummary.from_record(record, self._public_coordinates)
                for record in page.items
            ),
            next_cursor=page.next_cursor,
        )
