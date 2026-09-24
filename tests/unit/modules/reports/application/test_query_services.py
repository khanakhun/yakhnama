"""Unit tests for the authorised report queries and their privacy rules."""

from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.fakes.reports import InMemoryReportQueryService, InMemoryReportsUnitOfWork
from tests.unit.modules.reports.application.support import (
    ACCURACY,
    COLLEAGUE,
    MEMBER,
    MISSING_ID,
    MODERATOR,
    NOW,
    OBSERVED,
    ORGANIZATION_ID,
    OTHER_CITIZEN,
    OTHER_ID,
    POINT,
    PUBLIC_COORDINATES,
    REPORTER,
    ROUNDED_POINT,
    stored_report,
)
from yakhnama.modules.identity.public import Actor
from yakhnama.modules.reports.application.queries import GetReport, ListReports
from yakhnama.modules.reports.application.query_services import (
    AuthorisedReportQueryService,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import ReportNotFoundError
from yakhnama.modules.reports.domain.value_objects import (
    HazardGuess,
    ObservationPoint,
    ReportStatus,
    TriageResult,
)
from yakhnama.shared_kernel.errors import PermissionDeniedError
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.value_objects import (
    BoundingBox,
    Confidence,
    Coordinates,
    DateWithPrecision,
)

TRIAGE = TriageResult(evaluated_at=NOW)


def service(*reports: Report) -> AuthorisedReportQueryService:
    """Return the authorised service over a fake holding ``reports``."""
    uow = InMemoryReportsUnitOfWork(reports=reports)
    return AuthorisedReportQueryService(
        InMemoryReportQueryService(uow), PUBLIC_COORDINATES
    )


def at(point: Coordinates, **overrides: object) -> Report:
    """Return a stored report by ``REPORTER_ID`` observed at ``point``."""
    return stored_report(
        observation=ObservationPoint(coordinates=point, accuracy=ACCURACY), **overrides
    )


# --------------------------------------------------------------------------- #
# GetReport                                                                   #
# --------------------------------------------------------------------------- #


async def test_get_report_reporter_sees_exact_position_without_triage() -> None:
    report = stored_report(triage=TRIAGE)

    result = await service(report).get_report(
        GetReport(actor=REPORTER, report_id=report.id)
    )

    assert result.coordinates == POINT
    assert result.accuracy == ACCURACY
    assert result.coordinates_are_exact is True
    assert result.triage is None


async def test_get_report_moderator_sees_exact_position_and_triage() -> None:
    report = stored_report(triage=TRIAGE)

    result = await service(report).get_report(
        GetReport(actor=MODERATOR, report_id=report.id)
    )

    assert result.coordinates == POINT
    assert result.coordinates_are_exact is True
    assert result.triage == TRIAGE


async def test_get_report_organisation_colleague_sees_rounded_position() -> None:
    report = stored_report(organization_id=ORGANIZATION_ID, triage=TRIAGE)

    result = await service(report).get_report(
        GetReport(actor=COLLEAGUE, report_id=report.id)
    )

    assert result.coordinates == ROUNDED_POINT
    assert result.accuracy is None
    assert result.coordinates_are_exact is False
    assert result.triage is None


async def test_get_report_other_citizen_is_told_it_does_not_exist() -> None:
    report = stored_report()

    with pytest.raises(ReportNotFoundError):
        await service(report).get_report(
            GetReport(actor=OTHER_CITIZEN, report_id=report.id)
        )


async def test_get_report_non_member_of_organisation_is_told_it_does_not_exist() -> (
    None
):
    report = stored_report(organization_id=ORGANIZATION_ID)

    with pytest.raises(ReportNotFoundError):
        await service(report).get_report(
            GetReport(actor=OTHER_CITIZEN, report_id=report.id)
        )


async def test_get_report_anonymous_raises_permission_denied() -> None:
    report = stored_report()

    with pytest.raises(PermissionDeniedError):
        await service(report).get_report(
            GetReport(actor=Actor.anonymous(), report_id=report.id)
        )


async def test_get_report_missing_raises_not_found() -> None:
    with pytest.raises(ReportNotFoundError):
        await service().get_report(GetReport(actor=MODERATOR, report_id=MISSING_ID))


# --------------------------------------------------------------------------- #
# ListReports                                                                 #
# --------------------------------------------------------------------------- #


async def test_list_reports_citizen_sees_only_own_reports_rounded() -> None:
    own = stored_report()
    other = stored_report(reporter_id=OTHER_ID)

    page = await service(own, other).list_reports(ListReports(actor=REPORTER))

    [summary] = page.items
    assert summary.id == own.id
    assert summary.coordinates == ROUNDED_POINT
    assert "accuracy" not in type(summary).model_fields


async def test_list_reports_moderator_sees_every_report_newest_first() -> None:
    older = stored_report(created_at=NOW - timedelta(hours=3))
    newer = stored_report(reporter_id=OTHER_ID, created_at=NOW - timedelta(hours=2))

    page = await service(older, newer).list_reports(ListReports(actor=MODERATOR))

    assert [item.id for item in page.items] == [newer.id, older.id]
    assert all(item.coordinates == ROUNDED_POINT for item in page.items)


async def test_list_reports_pages_with_cursor() -> None:
    reports = [stored_report(created_at=NOW - timedelta(hours=h)) for h in (1, 2, 3)]
    reports_service = service(*reports)

    first = await reports_service.list_reports(
        ListReports(actor=MEMBER, page=PageRequest(limit=2))
    )
    second = await reports_service.list_reports(
        ListReports(actor=MEMBER, page=PageRequest(limit=2, cursor=first.next_cursor))
    )

    assert [item.id for item in first.items] == [reports[0].id, reports[1].id]
    assert [item.id for item in second.items] == [reports[2].id]
    assert second.next_cursor is None


async def test_list_reports_anonymous_raises_permission_denied() -> None:
    with pytest.raises(PermissionDeniedError):
        await service().list_reports(ListReports(actor=Actor.anonymous()))


async def test_list_reports_status_and_hazard_filters_combine() -> None:
    guess = HazardGuess(hazard_code="glof", confidence=Confidence.LOW)
    match = stored_report(hazard_guess=guess)
    no_guess = stored_report()
    withdrawn = stored_report(
        hazard_guess=guess,
        status=ReportStatus.WITHDRAWN,
        withdrawal_reason="Not sure.",
    )

    page = await service(match, no_guess, withdrawn).list_reports(
        ListReports(actor=MODERATOR, status=ReportStatus.SUBMITTED, hazard_code="glof")
    )

    assert [item.id for item in page.items] == [match.id]
    assert page.items[0].hazard_code == "glof"


async def test_list_reports_bbox_tests_rounded_not_exact_position() -> None:
    # Exactly inside the box, but rounds to 35.92, below it.
    exact_inside = at(Coordinates(longitude=74.3, latitude=35.9212))
    # Exactly above the box, but rounds to 35.93, on its edge.
    exact_outside = at(Coordinates(longitude=74.3, latitude=35.9301))
    bbox = BoundingBox(
        min_longitude=74.0,
        min_latitude=35.921,
        max_longitude=74.5,
        max_latitude=35.93,
    )

    page = await service(exact_inside, exact_outside).list_reports(
        ListReports(actor=MODERATOR, bbox=bbox)
    )

    assert [item.id for item in page.items] == [exact_outside.id]


async def test_list_reports_time_range_keeps_reports_inside_it() -> None:
    early = stored_report(
        observed_at=DateWithPrecision(
            value=OBSERVED.value - timedelta(days=3), precision=OBSERVED.precision
        ),
        created_at=NOW - timedelta(hours=1),
    )
    inside = stored_report()
    late_bound = OBSERVED.value + timedelta(minutes=1)

    page = await service(early, inside).list_reports(
        ListReports(
            actor=MODERATOR,
            observed_from=OBSERVED.value - timedelta(days=1),
            observed_to=late_bound,
        )
    )

    assert [item.id for item in page.items] == [inside.id]


async def test_list_reports_observed_to_excludes_later_reports() -> None:
    report = stored_report()

    page = await service(report).list_reports(
        ListReports(actor=MODERATOR, observed_to=OBSERVED.value - timedelta(hours=1))
    )

    assert page.items == ()


def test_list_reports_inverted_time_range_raises_validation_error() -> None:
    with pytest.raises(PydanticValidationError):
        ListReports(
            actor=REPORTER,
            observed_from=NOW,
            observed_to=NOW - timedelta(days=1),
        )
