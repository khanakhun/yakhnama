"""Unit tests for the report specifications and how ``ListReports`` combines them."""

from datetime import timedelta

from tests.unit.modules.reports.application.support import (
    NOW,
    PUBLIC_COORDINATES,
    REPORTER,
    REPORTER_ID,
    stored_report,
)
from yakhnama.modules.reports.application.dto import ReportRecord
from yakhnama.modules.reports.application.queries import ListReports
from yakhnama.modules.reports.application.specifications import (
    ReportHazardCodeSpecification,
    ReportInBoundingBoxSpecification,
    ReportObservedFromSpecification,
    ReportObservedToSpecification,
    ReportReporterSpecification,
    ReportStatusSpecification,
)
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.specification import AndSpecification, TrueSpecification
from yakhnama.shared_kernel.value_objects import BoundingBox

BBOX = BoundingBox(
    min_longitude=74.0, min_latitude=35.0, max_longitude=75.0, max_latitude=36.0
)


def record() -> ReportRecord:
    """Return the record of a default stored report."""
    return ReportRecord.from_entity(stored_report())


def test_specifications_expose_their_parameters() -> None:
    instant = NOW - timedelta(days=1)

    status = ReportStatusSpecification(ReportStatus.SUBMITTED)
    hazard = ReportHazardCodeSpecification("glof")
    box = ReportInBoundingBoxSpecification(BBOX, PUBLIC_COORDINATES)
    since = ReportObservedFromSpecification(instant)
    until = ReportObservedToSpecification(instant)
    reporter = ReportReporterSpecification(REPORTER_ID)

    assert status.status is ReportStatus.SUBMITTED
    assert hazard.hazard_code == "glof"
    assert box.bbox == BBOX
    assert box.public_coordinates == PUBLIC_COORDINATES
    assert since.instant == instant
    assert until.instant == instant
    assert reporter.reporter_id == REPORTER_ID


def test_reporter_specification_matches_own_record() -> None:
    specification = ReportReporterSpecification(REPORTER_ID)

    result = specification.is_satisfied_by(record())

    assert result is True


def test_list_reports_without_filters_builds_true_specification() -> None:
    query = ListReports(actor=REPORTER)

    specification = query.to_specification(PUBLIC_COORDINATES)

    assert isinstance(specification, TrueSpecification)
    assert specification.is_satisfied_by(record())


def test_list_reports_with_filters_builds_conjunction() -> None:
    query = ListReports(actor=REPORTER, status=ReportStatus.SUBMITTED, bbox=BBOX)

    specification = query.to_specification(PUBLIC_COORDINATES)

    assert isinstance(specification, AndSpecification)
    assert specification.is_satisfied_by(record())
