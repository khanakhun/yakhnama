"""Read requests accepted by the reports query services.

Patterns: Query, Specification.
"""

from datetime import timedelta
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.modules.reports.application.specifications import (
    ReportHazardCodeSpecification,
    ReportInBoundingBoxSpecification,
    ReportObservedFromSpecification,
    ReportObservedToSpecification,
    ReportStatusSpecification,
)
from yakhnama.modules.reports.domain.triage import NEARBY_REPORTS_MAX
from yakhnama.modules.reports.domain.value_objects import (
    GuessedHazardCode,
    ReportStatus,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.privacy import PublicCoordinatePolicy
from yakhnama.shared_kernel.specification import Specification, TrueSpecification
from yakhnama.shared_kernel.value_objects import BoundingBox, Coordinates


class GetReport(BaseModel):
    """Ask for one report.

    Implements: Query.

    Attributes:
        actor: Who asks.
        report_id: The report.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_id: EntityId


class ListReports(BaseModel):
    """Ask for one page of reports, newest first, with optional filters.

    Implements: Query.

    Attributes:
        actor: Who asks; non-moderators only ever see their own reports.
        status: Only reports in this status, if set.
        hazard_code: Only reports whose reporter guessed this hazard type, if set.
        bbox: Only reports whose *rounded* position lies in this box, if set.
        observed_from: Only reports observed at or after this instant, if set.
        observed_to: Only reports observed at or before this instant, if set.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    status: ReportStatus | None = None
    hazard_code: GuessedHazardCode | None = None
    bbox: BoundingBox | None = None
    observed_from: AwareDatetime | None = None
    observed_to: AwareDatetime | None = None
    page: PageRequest = PageRequest()

    @model_validator(mode="after")
    def _check_time_range(self) -> Self:
        if (
            self.observed_from is not None
            and self.observed_to is not None
            and self.observed_from > self.observed_to
        ):
            message = "observed_from must not be later than observed_to"
            raise ValueError(message)
        return self

    def to_specification(
        self, public_coordinates: PublicCoordinatePolicy
    ) -> Specification[ReportRecord]:
        """Combine the set filters into one specification.

        Args:
            public_coordinates: The rounding the bounding-box filter applies.

        Returns:
            The conjunction of every set filter, or a specification every report
            satisfies when none is set.
        """
        filters: list[Specification[ReportRecord]] = []
        if self.status is not None:
            filters.append(ReportStatusSpecification(self.status))
        if self.hazard_code is not None:
            filters.append(ReportHazardCodeSpecification(self.hazard_code))
        if self.bbox is not None:
            filters.append(
                ReportInBoundingBoxSpecification(self.bbox, public_coordinates)
            )
        if self.observed_from is not None:
            filters.append(ReportObservedFromSpecification(self.observed_from))
        if self.observed_to is not None:
            filters.append(ReportObservedToSpecification(self.observed_to))
        combined: Specification[ReportRecord] = TrueSpecification[ReportRecord]()
        for specification in filters:
            combined = combined.and_(specification)
        return combined


class FindNearbyReports(BaseModel):
    """Ask the read side for current reports near a point in space and time.

    Sent by ``RunTriageHandler`` to the ``NearbyReportsFinder`` port.

    Implements: Query.

    Attributes:
        center: The triaged report's exact position.
        observed_at: The triaged report's observation time.
        exclude_report_id: The triaged report itself.
        radius_metres: Great-circle radius around ``center``.
        window: Largest time difference from ``observed_at``.
        limit: Most candidates to return.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    center: Coordinates
    observed_at: AwareDatetime
    exclude_report_id: EntityId
    radius_metres: float = Field(gt=0, allow_inf_nan=False)
    window: timedelta
    limit: int = Field(ge=1, le=NEARBY_REPORTS_MAX)
