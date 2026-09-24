"""Unit tests for ``yakhnama.modules.media.domain.factories``."""

from datetime import UTC, datetime

from tests.factories.base import FACTORY_IDS
from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from yakhnama.modules.media.domain.events import UploadRequested
from yakhnama.modules.media.domain.factories import MediaAssetFactory
from yakhnama.modules.media.domain.value_objects import (
    MediaAttribution,
    MimeType,
    ModerationStatus,
    ScanStatus,
    UploadStatus,
    original_object_key,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_media_asset_factory_request_upload_returns_requested_asset_and_event() -> None:
    attribution = MediaAttribution(
        owner_id=FACTORY_IDS.new_id(),
        source_id=FACTORY_IDS.new_id(),
        report_id=FACTORY_IDS.new_id(),
    )

    change = MediaAssetFactory().request_upload(
        attribution,
        MimeType.WEBP,
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(seed=5),
    )

    asset = change.state
    assert asset.upload_status is UploadStatus.REQUESTED
    assert asset.scan_status is ScanStatus.PENDING
    assert asset.moderation_status is ModerationStatus.PENDING
    assert asset.original_key == original_object_key(asset.id)
    assert asset.report_id == attribution.report_id
    assert asset.created_at == asset.updated_at == NOW
    (event,) = change.events
    assert isinstance(event, UploadRequested)
    assert event.aggregate_id == asset.id
    assert event.event_id != asset.id
    assert event.mime_type is MimeType.WEBP
    assert event.event_type == "media.upload_requested"
    assert "media/" not in event.model_dump_json()
