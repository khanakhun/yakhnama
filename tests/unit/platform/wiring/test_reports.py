"""Unit tests for ``yakhnama.platform.wiring.reports`` over the media fakes."""

from datetime import UTC, datetime
from typing import Final

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.media import MediaAssetTestFactory, synthetic_sha256
from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import InMemoryMediaQueryService, InMemoryMediaUnitOfWork
from tests.fakes.reports import (
    FakeNearbyReportsFinder,
    FakePhotoEvidenceProvider,
    InMemoryReportsUnitOfWork,
)
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.media.public import ExifFacts, MediaAsset, UploadStatus
from yakhnama.modules.reports.public import (
    RUN_TRIAGE_TASK,
    PhotoEvidence,
    ReportNotFoundError,
    RunTriageHandler,
)
from yakhnama.platform.tasks.handlers import REPORTS_TRIAGE_TASK
from yakhnama.platform.wiring.reports import (
    MediaOwnershipAdapter,
    PhotoEvidenceAdapter,
    RunTriageTaskAdapter,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId
from yakhnama.shared_kernel.value_objects import (
    Coordinates,
    DatePrecision,
    DateWithPrecision,
)

IDS: Final = SequentialIdGenerator(seed=301)
OWNER: Final = IDS.new_id()
STRANGER: Final = IDS.new_id()
CLOCK: Final = FrozenClock(datetime(2026, 7, 1, 12, 0, tzinfo=UTC))
TAKEN_AT: Final = DateWithPrecision(value=CLOCK.now(), precision=DatePrecision.EXACT)
LOCATION: Final = Coordinates(longitude=74.6, latitude=36.3)


def _asset(
    owner_id: EntityId = OWNER,
    *,
    upload_status: UploadStatus = UploadStatus.COMPLETED,
    exif: ExifFacts | None = None,
) -> MediaAsset:
    is_completed = upload_status is UploadStatus.COMPLETED
    return MediaAssetTestFactory.build(
        owner_id=owner_id,
        upload_status=upload_status,
        exif=exif,
        # A completed upload carries its digest and size (a MediaAsset invariant).
        sha256=synthetic_sha256() if is_completed else None,
        byte_size=2048 if is_completed else None,
    )


def _media(*assets: MediaAsset) -> InMemoryMediaQueryService:
    return InMemoryMediaQueryService(InMemoryMediaUnitOfWork(assets=assets))


def _task(payload: dict[str, object]) -> ScheduledTask:
    return ScheduledTask.model_validate(
        {
            "task_id": TaskId(value="t-1"),
            "task_name": REPORTS_TRIAGE_TASK,
            "payload": payload,
        }
    )


async def test_media_ownership_adapter_all_assets_owned_returns_true() -> None:
    first, second = _asset(), _asset()
    adapter = MediaOwnershipAdapter(_media(first, second))

    result = await adapter.is_owned_by([first.id, second.id], OWNER)

    assert result is True


async def test_media_ownership_adapter_no_assets_returns_true() -> None:
    adapter = MediaOwnershipAdapter(_media())

    result = await adapter.is_owned_by([], OWNER)

    assert result is True


async def test_media_ownership_adapter_someone_elses_asset_returns_false() -> None:
    mine, theirs = _asset(), _asset(STRANGER)
    adapter = MediaOwnershipAdapter(_media(mine, theirs))

    result = await adapter.is_owned_by([mine.id, theirs.id], OWNER)

    assert result is False


async def test_media_ownership_adapter_missing_asset_returns_false() -> None:
    mine = _asset()
    adapter = MediaOwnershipAdapter(_media(mine))

    result = await adapter.is_owned_by([mine.id, IDS.new_id()], OWNER)

    assert result is False


async def test_media_ownership_adapter_repeated_id_is_checked_once() -> None:
    mine = _asset()
    adapter = MediaOwnershipAdapter(_media(mine))

    result = await adapter.is_owned_by([mine.id, mine.id], OWNER)

    assert result is True


async def test_photo_evidence_adapter_returns_exif_facts_in_request_order() -> None:
    with_exif = _asset(exif=ExifFacts(taken_at=TAKEN_AT, location=LOCATION))
    without_exif = _asset()
    adapter = PhotoEvidenceAdapter(_media(with_exif, without_exif))

    photos = await adapter.photos_for([without_exif.id, with_exif.id], OWNER)

    assert photos == (
        PhotoEvidence(media_id=without_exif.id),
        PhotoEvidence(media_id=with_exif.id, taken_at=TAKEN_AT, location=LOCATION),
    )


async def test_photo_evidence_adapter_leaves_out_unusable_assets() -> None:
    theirs = _asset(STRANGER, exif=ExifFacts(location=LOCATION))
    pending = _asset(upload_status=UploadStatus.REQUESTED)
    adapter = PhotoEvidenceAdapter(_media(theirs, pending))

    photos = await adapter.photos_for([theirs.id, pending.id, IDS.new_id()], OWNER)

    assert photos == ()


async def test_photo_evidence_adapter_no_media_returns_empty_without_reading() -> None:
    adapter = PhotoEvidenceAdapter(_media())

    photos = await adapter.photos_for([], OWNER)

    assert photos == ()


async def test_photo_evidence_adapter_repeated_id_yields_one_entry() -> None:
    mine = _asset()
    adapter = PhotoEvidenceAdapter(_media(mine))

    photos = await adapter.photos_for([mine.id, mine.id], OWNER)

    assert photos == (PhotoEvidence(media_id=mine.id),)


def _triage_task_adapter(reports: InMemoryReportsUnitOfWork) -> RunTriageTaskAdapter:
    return RunTriageTaskAdapter(
        RunTriageHandler(
            uow_factory=InMemoryUnitOfWorkFactory(reports),
            nearby_reports=FakeNearbyReportsFinder(),
            photos=FakePhotoEvidenceProvider(),
            clock=CLOCK,
            ids=IDS,
        )
    )


async def test_run_triage_task_adapter_attaches_triage_to_the_named_report() -> None:
    report = ReportTestFactory.build()
    reports = InMemoryReportsUnitOfWork(reports=[report])

    await _triage_task_adapter(reports)(_task({"report_id": str(report.id)}))

    assert reports.reports.committed[report.id].triage is not None


async def test_run_triage_task_adapter_unknown_report_raises_not_found() -> None:
    adapter = _triage_task_adapter(InMemoryReportsUnitOfWork())

    with pytest.raises(ReportNotFoundError):
        await adapter(_task({"report_id": str(IDS.new_id())}))


async def test_run_triage_task_adapter_invalid_payload_raises_validation_error() -> (
    None
):
    adapter = _triage_task_adapter(InMemoryReportsUnitOfWork())

    with pytest.raises(PydanticValidationError):
        await adapter(_task({"report": "not-an-id"}))


def test_reports_triage_task_name_matches_the_reports_facade() -> None:
    assert REPORTS_TRIAGE_TASK == RUN_TRIAGE_TASK
