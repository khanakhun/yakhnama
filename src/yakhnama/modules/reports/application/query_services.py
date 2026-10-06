"""Authorised read use cases of the reports module, with the privacy rules applied.

``AuthorisedReportQueryService`` asks the policies in ``authorisation`` about the
query's actor, reads internal ``ReportRecord`` values through the
``ReportQueryService`` port and returns only ``ReportSummary`` and ``ReportDetail``
built with the right precision (see ``dto``). A report the actor may not read is
reported as missing, so its existence is not revealed.

Moderators also get the review mark of each report's lineage and, on a detail,
the events the lineage is linked to; they alone may filter on the mark and read
a lineage's review history (ADR 0022).

Patterns: Query Service, Policy.
"""

from typing import Final

from yakhnama.modules.reports.application.authorisation import (
    exact_view_policy,
    is_moderator,
    require_allowed,
    require_user,
    review_policy,
    rounded_view_policy,
    triage_view_policy,
)
from yakhnama.modules.reports.application.dto import (
    REVIEW_HISTORY_MAX,
    ModeratorView,
    ReportDetail,
    ReportReviewDetail,
    ReportReviewSummary,
    ReportSummary,
)
from yakhnama.modules.reports.application.ports import ReportQueryService
from yakhnama.modules.reports.application.queries import (
    GetReport,
    GetReportReview,
    ListReports,
)
from yakhnama.modules.reports.application.specifications import (
    ReportReporterSpecification,
)
from yakhnama.modules.reports.domain.errors import (
    ReportFilterForbiddenError,
    ReportNotFoundError,
)
from yakhnama.shared_kernel.errors import InvariantViolationError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy

REVIEW_STATE_FILTER: Final = "review_state"
"""The query parameter only moderators may send."""


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
                record,
                public_coordinates=None,
                is_triage_visible=is_triage_visible,
                moderation=await self._moderator_view(query, record.lineage_id),
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
            The page; for a non-moderator, or with ``is_own_only``, only the
            actor's own reports. Moderators see each lineage's review mark.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            ReportFilterForbiddenError: If a non-moderator filters on the review
                mark.
            ValidationError: If the cursor is invalid.
        """
        user_id = require_user(query.actor, action="list reports")
        is_reviewer = is_moderator(query.actor)
        if query.review_state is not None and not is_reviewer:
            raise ReportFilterForbiddenError.for_filter(REVIEW_STATE_FILTER)
        specification = query.to_specification(self._public_coordinates)
        if query.is_own_only or not is_reviewer:
            specification = specification.and_(ReportReporterSpecification(user_id))
        page = await self._query_service.list_reports(specification, query.page)
        return Page[ReportSummary](
            items=tuple(
                ReportSummary.from_record(
                    record, self._public_coordinates, is_review_visible=is_reviewer
                )
                for record in page.items
            ),
            next_cursor=page.next_cursor,
        )

    async def get_review(self, query: GetReportReview) -> ReportReviewDetail:
        """Return the review of a report's lineage with its history; moderators only.

        Args:
            query: The actor and any revision of the lineage.

        Returns:
            The current mark, the revision it was made on, whether the lineage
            was revised since, the review's version (0 while never marked) and
            up to ``REVIEW_HISTORY_MAX`` marks, newest first.

        Raises:
            PermissionDeniedError: If the actor may not moderate.
            ReportNotFoundError: If the report does not exist.
        """
        require_allowed(review_policy(), query.actor, action="read report reviews")
        record = await self._query_service.get_report(query.report_id)
        if record is None:
            raise ReportNotFoundError.for_id(query.report_id)
        lineage_id = _lineage_of(record.id, record.lineage_id)
        marks = await self._query_service.list_review_marks(
            lineage_id, REVIEW_HISTORY_MAX + 1
        )
        summary = ReportReviewSummary.from_record(record.review)
        return ReportReviewDetail(
            report_id=record.id,
            lineage_id=lineage_id,
            state=summary.state,
            updated_at=summary.updated_at,
            updated_by=summary.updated_by,
            reviewed_revision=summary.reviewed_revision,
            revised_since=summary.revised_since,
            version=0 if record.review is None else record.review.version,
            history=marks[:REVIEW_HISTORY_MAX],
            is_history_truncated=len(marks) > REVIEW_HISTORY_MAX,
        )

    async def _moderator_view(
        self, query: GetReport, lineage_id: EntityId | None
    ) -> ModeratorView | None:
        if not review_policy().is_allowed(query.actor):
            return None
        links = await self._query_service.list_linked_events(
            _lineage_of(query.report_id, lineage_id)
        )
        return ModeratorView(linked_events=links)


def _lineage_of(report_id: EntityId, lineage_id: EntityId | None) -> EntityId:
    # The read side always loads the lineage; a record without one would make
    # every review read and link lookup silently wrong.
    if lineage_id is None:
        message = "a stored report was read without its lineage"
        raise InvariantViolationError(message, details={"report_id": str(report_id)})
    return lineage_id
