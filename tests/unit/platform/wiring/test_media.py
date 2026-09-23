"""Unit tests for ``yakhnama.platform.wiring.media`` over reports and media fakes."""

from datetime import UTC, datetime
from typing import Final

import pytest
from pydantic import ValidationError as PydanticValidationError

from tests.factories.media import MediaAssetTestFactory, synthetic_sha256
from tests.factories.reports import ReportTestFactory
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import InMemoryMediaQueryService, InMemoryMediaUnitOfWork
from tests.fakes.reports import InMemoryReportQueryService, InMemoryReportsUnitOfWork
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.media.public import (
    SCAN_TASK,
    MediaAsset,
    MediaAssetNotFoundError,
    RecordScanResultHandler,
    ScanStatus,
    UploadStatus,
)
from yakhnama.platform.tasks.handlers import MEDIA_SCAN_TASK
from yakhnama.platform.wiring.media import ReportSourceAdapter, ScanTaskAdapter
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId

IDS: Final = SequentialIdGenerator(seed=401)
CLOCK: Final = FrozenClock(datetime(2026, 7, 1, 12, 0, tzinfo=UTC))


class RecordingScanner:
    """``MalwareScanner`` returning one verdict and recording the keys it scanned.

    Implements: Fake.

    Attributes:
        scanned: Every key scanned, in order.
    """

    def __init__(self, verdict: ScanStatus) -> None:
        """Create the scanner.

        Args:
            verdict: What every scan answers.
        """
        self._verdict = verdict
        self.scanned: list[str] = []

    async def scan(self, key: str) -> ScanStatus:
        """Record ``key`` and return the verdict.

        Args:
            key: The original's key.

        Returns:
            The configured verdict.
        """
        self.scanned.append(key)
        return self._verdict


async def test_report_source_adapter_own_report_returns_its_source() -> None:
    report = ReportTestFactory.build()
    adapter = ReportSourceAdapter(
        InMemoryReportQueryService(InMemoryReportsUnitOfWork(reports=[report]))
    )

    source_id = await adapter.find_source_for_reporter(report.id, report.reporter_id)

    assert source_id == report.source_id


async def test_report_source_adapter_someone_elses_report_returns_none() -> None:
    report = ReportTestFactory.build()
    adapter = ReportSourceAdapter(
        InMemoryReportQueryService(InMemoryReportsUnitOfWork(reports=[report]))
    )

    source_id = await adapter.find_source_for_reporter(report.id, IDS.new_id())

    assert source_id is None


async def test_report_source_adapter_missing_report_returns_none() -> None:
    adapter = ReportSourceAdapter(
        InMemoryReportQueryService(InMemoryReportsUnitOfWork())
    )

    source_id = await adapter.find_source_for_reporter(IDS.new_id(), IDS.new_id())

    assert source_id is None


def _completed_asset() -> MediaAsset:
    return MediaAssetTestFactory.build(
        upload_status=UploadStatus.COMPLETED,
        sha256=synthetic_sha256(),
        byte_size=2048,
    )


def _scan_adapter(
    media: InMemoryMediaUnitOfWork, scanner: RecordingScanner
) -> ScanTaskAdapter:
    return ScanTaskAdapter(
        media=InMemoryMediaQueryService(media),
        scanner=scanner,
        record_scan_result=RecordScanResultHandler(
            InMemoryUnitOfWorkFactory(media), CLOCK, IDS
        ),
    )


def _task(payload: dict[str, object]) -> ScheduledTask:
    return ScheduledTask.model_validate(
        {
            "task_id": TaskId(value="t-1"),
            "task_name": MEDIA_SCAN_TASK,
            "payload": payload,
        }
    )


async def test_scan_task_adapter_scans_the_original_and_records_the_verdict() -> None:
    asset = _completed_asset()
    media = InMemoryMediaUnitOfWork(assets=[asset])
    scanner = RecordingScanner(ScanStatus.CLEAN)

    await _scan_adapter(media, scanner)(_task({"asset_id": str(asset.id)}))

    assert scanner.scanned == [asset.original_key]
    assert media.media_assets.committed[asset.id].scan_status is ScanStatus.CLEAN


async def test_scan_task_adapter_unknown_asset_raises_not_found_without_scanning() -> (
    None
):
    scanner = RecordingScanner(ScanStatus.CLEAN)
    adapter = _scan_adapter(InMemoryMediaUnitOfWork(), scanner)

    with pytest.raises(MediaAssetNotFoundError):
        await adapter(_task({"asset_id": str(IDS.new_id())}))

    assert scanner.scanned == []


async def test_scan_task_adapter_invalid_payload_raises_validation_error() -> None:
    adapter = _scan_adapter(
        InMemoryMediaUnitOfWork(), RecordingScanner(ScanStatus.CLEAN)
    )

    with pytest.raises(PydanticValidationError):
        await adapter(_task({"asset_id": "not-an-id"}))


def test_media_scan_task_name_matches_the_media_facade() -> None:
    assert MEDIA_SCAN_TASK == SCAN_TASK
