"""Unit tests for the triage flag kinds on report listings and their filter."""

import pytest

from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.reports import InMemoryReportQueryService
from tests.unit.modules.reports.application.support import (
    MODERATOR,
    NOW,
    PUBLIC_COORDINATES,
    REPORTER,
    Harness,
    stored_report,
)
from yakhnama.modules.reports.application.queries import ListReports
from yakhnama.modules.reports.application.query_services import (
    AuthorisedReportQueryService,
)
from yakhnama.modules.reports.application.specifications import (
    ReportTriageFlagSpecification,
)
from yakhnama.modules.reports.domain.entities import Report
from yakhnama.modules.reports.domain.errors import ReportFilterForbiddenError
from yakhnama.modules.reports.domain.value_objects import (
    TriageFlag,
    TriageFlagKind,
    TriageResult,
)
from yakhnama.shared_kernel.value_objects import Confidence

IDS = SequentialIdGenerator(seed=2501)


def _flag(kind: TriageFlagKind) -> TriageFlag:
    return TriageFlag(kind=kind, detail="Found by a rule.", confidence=Confidence.LOW)


def _triaged(*kinds: TriageFlagKind) -> Report:
    return stored_report(
        id=IDS.new_id(),
        triage=TriageResult(flags=tuple(map(_flag, kinds)), evaluated_at=NOW),
    )


def _queries(harness: Harness) -> AuthorisedReportQueryService:
    return AuthorisedReportQueryService(
        InMemoryReportQueryService(harness.uow), PUBLIC_COORDINATES
    )


async def test_list_reports_gives_moderators_distinct_flag_kinds_without_detail() -> (
    None
):
    flagged = _triaged("pii_detected", "spam_suspected", "pii_detected")
    untriaged = stored_report(id=IDS.new_id())
    harness = Harness(flagged, untriaged)

    page = await _queries(harness).list_reports(ListReports(actor=MODERATOR))

    kinds = {item.id: item.triage_flags for item in page.items}
    assert kinds == {
        flagged.id: ("pii_detected", "spam_suspected"),
        untriaged.id: (),
    }


async def test_list_reports_hides_flag_kinds_from_reporters() -> None:
    harness = Harness(_triaged("spam_suspected"))

    page = await _queries(harness).list_reports(ListReports(actor=REPORTER))

    assert page.items[0].triage_flags is None


async def test_list_reports_triage_flag_filter_keeps_only_flagged_reports() -> None:
    spam = _triaged("spam_suspected")
    duplicate = _triaged("duplicate_suspected", "exif_implausible")
    harness = Harness(spam, duplicate, stored_report(id=IDS.new_id()))

    page = await _queries(harness).list_reports(
        ListReports(actor=MODERATOR, triage_flag="duplicate_suspected")
    )

    assert [item.id for item in page.items] == [duplicate.id]


async def test_list_reports_triage_flag_filter_by_reporter_is_forbidden() -> None:
    harness = Harness(_triaged("spam_suspected"))

    with pytest.raises(ReportFilterForbiddenError) as raised:
        await _queries(harness).list_reports(
            ListReports(actor=REPORTER, triage_flag="spam_suspected")
        )

    assert "triage_flag" in str(raised.value)


def test_triage_flag_specification_exposes_its_kind() -> None:
    assert ReportTriageFlagSpecification("pii_detected").kind == "pii_detected"
