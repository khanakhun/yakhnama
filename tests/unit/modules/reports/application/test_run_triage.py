"""Unit tests for ``RunTriageHandler`` with crafted triage contexts."""

from datetime import timedelta

import pytest

from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.reports import FakePhotoEvidenceProvider
from tests.unit.modules.reports.application.support import (
    MEDIA_ID,
    MISSING_ID,
    NOW,
    OBSERVED,
    POINT,
    REPORTER_ID,
    Harness,
    stored_report,
)
from yakhnama.modules.reports.application.commands import RunTriage
from yakhnama.modules.reports.domain.errors import ReportNotFoundError
from yakhnama.modules.reports.domain.events import ReportTriaged
from yakhnama.modules.reports.domain.triage import (
    DUPLICATE_DISTANCE_METRES,
    DUPLICATE_TIME_WINDOW,
    NEARBY_REPORTS_MAX,
    PhotoEvidence,
    ReportSummaryForTriage,
)
from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.value_objects import Coordinates

OTHER_REPORT_ID = SequentialIdGenerator(seed=521).new_id()
# About 110 m north of POINT: well inside the 2 km duplicate radius.
NEAR_POINT = Coordinates(longitude=POINT.longitude, latitude=POINT.latitude + 0.001)
# About 111 km north of POINT: far outside the 5 km EXIF radius.
FAR_POINT = Coordinates(longitude=POINT.longitude, latitude=POINT.latitude + 1.0)


async def test_run_triage_crafted_context_attaches_every_flag_in_order() -> None:
    report = stored_report(
        media_ids=(MEDIA_ID,),
        description="Call 0300 1234567 about the flood near the bridge.",
    )
    harness = Harness(report)
    harness.nearby.candidates = (
        ReportSummaryForTriage(
            report_id=OTHER_REPORT_ID, observed_at=OBSERVED, coordinates=NEAR_POINT
        ),
    )
    harness.photos = FakePhotoEvidenceProvider(
        {MEDIA_ID: (REPORTER_ID, PhotoEvidence(media_id=MEDIA_ID, location=FAR_POINT))}
    )

    result = await harness.triage()(RunTriage(report_id=report.id))

    assert result is not None
    assert result.kinds == ("exif_implausible", "duplicate_suspected", "pii_detected")
    stored = harness.uow.reports.committed[report.id]
    assert stored.triage == result
    assert stored.status is ReportStatus.SUBMITTED
    assert stored.content == report.content
    assert result.flags[1].related_report_id == OTHER_REPORT_ID
    assert [type(event) for event in harness.uow.committed_events] == [ReportTriaged]


async def test_run_triage_asks_finder_with_duplicate_thresholds() -> None:
    report = stored_report()
    harness = Harness(report)

    await harness.triage()(RunTriage(report_id=report.id))

    [query] = harness.nearby.queries
    assert query.center == POINT
    assert query.observed_at == OBSERVED.value
    assert query.exclude_report_id == report.id
    assert query.radius_metres == DUPLICATE_DISTANCE_METRES
    assert query.window == DUPLICATE_TIME_WINDOW
    assert query.limit == NEARBY_REPORTS_MAX


async def test_run_triage_photos_are_asked_for_the_reporters_assets() -> None:
    report = stored_report(media_ids=(MEDIA_ID,))
    harness = Harness(report)

    await harness.triage()(RunTriage(report_id=report.id))

    assert harness.photos.calls == [((MEDIA_ID,), REPORTER_ID)]


async def test_run_triage_without_media_does_not_ask_for_photos() -> None:
    report = stored_report()
    harness = Harness(report)

    result = await harness.triage()(RunTriage(report_id=report.id))

    assert harness.photos.calls == []
    assert result is not None
    assert result.flags == ()
    assert result.evaluated_at == NOW


async def test_run_triage_repeated_with_same_result_records_one_event() -> None:
    report = stored_report()
    harness = Harness(report)
    handler = harness.triage()

    await handler(RunTriage(report_id=report.id))
    await handler(RunTriage(report_id=report.id))

    assert [type(event) for event in harness.uow.committed_events] == [ReportTriaged]


async def test_run_triage_withdrawn_report_is_skipped() -> None:
    report = stored_report(
        status=ReportStatus.WITHDRAWN,
        withdrawal_reason="Sent twice.",
        updated_at=NOW - timedelta(minutes=5),
    )
    harness = Harness(report)

    result = await harness.triage()(RunTriage(report_id=report.id))

    assert result is None
    assert harness.uow.reports.committed[report.id] == report
    assert harness.nearby.queries == []


async def test_run_triage_missing_report_raises_not_found() -> None:
    harness = Harness()

    with pytest.raises(ReportNotFoundError):
        await harness.triage()(RunTriage(report_id=MISSING_ID))
