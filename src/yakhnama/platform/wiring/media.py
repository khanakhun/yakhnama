"""Adapters answering the media module's ports, and its scan task handler.

- ``ReportSourceAdapter`` (``ReportSourceLookup``): the source of a user's own
  report, from the reports facade, so an upload attached to a report cites it.
- ``ScanTaskAdapter``: the ``media.scan`` task handler. It reloads the asset,
  streams its original to the ``MalwareScanner`` together with the digest recorded
  at completion, and stores the verdict through ``RecordScanResultHandler``. If
  the bytes read no longer hash to that digest, it records the change instead,
  which quarantines the asset.

Patterns: Adapter.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.media.public import (
    MalwareScanner,
    MediaAssetNotFoundError,
    MediaContentChangedError,
    MediaQueryService,
    RecordScanResult,
    RecordScanResultHandler,
    ScanStatus,
)
from yakhnama.modules.reports.public import ReportQueryService
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.tasks import ScheduledTask


class ReportSourceAdapter:
    """``ReportSourceLookup`` over the reports read model.

    Implements: Adapter.
    """

    def __init__(self, reports: ReportQueryService) -> None:
        """Create the adapter.

        Args:
            reports: The reports module's read port.
        """
        self._reports = reports

    async def find_source_for_reporter(
        self, report_id: EntityId, reporter_id: EntityId
    ) -> EntityId | None:
        """Return the report's source id if ``reporter_id`` submitted the report.

        Args:
            report_id: The report.
            reporter_id: The would-be uploader.

        Returns:
            The report's ``source_id``, or ``None`` if the report does not exist
            or is someone else's.
        """
        record = await self._reports.get_report(report_id)
        if record is None or record.reporter_id != reporter_id:
            return None
        return record.source_id


class ScanTaskPayload(BaseModel):
    """The payload of a ``media.scan`` task.

    Implements: DTO.

    Attributes:
        asset_id: The uploaded asset to scan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: EntityId


class ScanTaskAdapter:
    """The ``media.scan`` task handler: scan the original, record the verdict.

    Delivery is at least once; a repeated verdict commits nothing
    (``RecordScanResultHandler``), so a repeat only costs a second scan. A
    changed original is recorded, not raised, so the task does not retry a scan
    that can never match.

    Implements: Adapter.
    """

    def __init__(
        self,
        *,
        media: MediaQueryService,
        scanner: MalwareScanner,
        record_scan_result: RecordScanResultHandler,
    ) -> None:
        """Create the task handler.

        Args:
            media: Supplies the asset's original key.
            scanner: The malware scanner bound for this process.
            record_scan_result: Stores the verdict.
        """
        self._media = media
        self._scanner = scanner
        self._record_scan_result = record_scan_result

    async def __call__(self, task: ScheduledTask) -> None:
        """Scan the asset named in the payload and record the verdict.

        Args:
            task: The task; payload ``{asset_id}``.

        Raises:
            pydantic.ValidationError: If the payload is not a ``ScanTaskPayload``.
            MediaAssetNotFoundError: If the asset does not exist.
            MediaUploadNotCompletedError: If its upload has not completed.
            StorageError: If storage cannot deliver the original.
        """
        payload = ScanTaskPayload.model_validate(dict(task.payload))
        record = await self._media.get_asset(payload.asset_id)
        if record is None:
            raise MediaAssetNotFoundError.for_id(payload.asset_id)
        try:
            verdict = await self._scanner.scan(
                record.original_key, expected_sha256=record.sha256
            )
        except MediaContentChangedError:
            await self._record_scan_result(
                RecordScanResult(
                    asset_id=payload.asset_id,
                    verdict=ScanStatus.UNAVAILABLE,
                    is_content_changed=True,
                )
            )
            return
        await self._record_scan_result(
            RecordScanResult(asset_id=payload.asset_id, verdict=verdict)
        )
