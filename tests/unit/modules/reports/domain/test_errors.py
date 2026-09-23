"""Unit tests for ``yakhnama.modules.reports.domain.errors``."""

import pytest

from tests.factories.base import FACTORY_IDS
from yakhnama.modules.reports.domain.errors import (
    ReportAlreadySubmittedError,
    ReportImmutableError,
    ReportNotFoundError,
    ReportNotSubmittedError,
    ReportRevisionUnchangedError,
    ReportSupersessionMismatchError,
    ReportWithdrawnError,
)
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
    YakhnamaError,
)

REPORT_ID = FACTORY_IDS.new_id()
OTHER_ID = FACTORY_IDS.new_id()


@pytest.mark.parametrize(
    ("error", "family", "status"),
    [
        (ReportNotFoundError.for_id(REPORT_ID), NotFoundError, None),
        (
            ReportImmutableError.for_report(REPORT_ID, ReportStatus.SUPERSEDED),
            InvalidTransitionError,
            "superseded",
        ),
        (
            ReportAlreadySubmittedError.for_report(REPORT_ID, ReportStatus.SUBMITTED),
            ConflictError,
            "submitted",
        ),
        (
            ReportWithdrawnError.for_report(REPORT_ID),
            InvalidTransitionError,
            "withdrawn",
        ),
        (
            ReportNotSubmittedError.for_report(REPORT_ID),
            InvalidTransitionError,
            "draft",
        ),
        (ReportRevisionUnchangedError.for_report(REPORT_ID), ValidationError, None),
        (
            ReportSupersessionMismatchError.for_reports(REPORT_ID, OTHER_ID),
            InvariantViolationError,
            None,
        ),
    ],
    ids=lambda value: type(value).__name__,
)
def test_report_error_builders_set_family_id_and_status(
    error: YakhnamaError, family: type[YakhnamaError], status: str | None
) -> None:
    details = dict(error.details)

    assert isinstance(error, family)
    assert details["report_id"] == str(REPORT_ID)
    assert details.get("status") == status
    assert str(REPORT_ID) not in error.message


def test_report_supersession_mismatch_details_name_the_successor() -> None:
    error = ReportSupersessionMismatchError.for_reports(REPORT_ID, OTHER_ID)

    successor = error.details["successor_id"]

    assert successor == str(OTHER_ID)
