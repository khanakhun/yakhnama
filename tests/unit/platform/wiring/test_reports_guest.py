"""Unit tests for ``GuestMediaGatewayAdapter`` over the media handlers and fakes."""

from datetime import UTC, datetime
from typing import Final

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import (
    FakeExifReader,
    FakeMimeSniffer,
    FakeStoragePort,
    InMemoryMediaUnitOfWork,
)
from tests.fakes.provenance import FakePlatformSourceRegistrar
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.media.public import (
    CompleteUploadHandler,
    MimeType,
    RequestGuestUploadHandler,
    StoredObject,
    UploadStatus,
    original_object_key,
    upload_object_key,
)
from yakhnama.platform.wiring.reports import GuestMediaGatewayAdapter

CLOCK: Final = FrozenClock(datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
SUBMISSION_ID: Final = SequentialIdGenerator(seed=961).new_id()


async def test_guest_media_gateway_grants_and_completes_a_submission_photo() -> None:
    media = InMemoryMediaUnitOfWork()
    storage = FakeStoragePort()
    sniffer = FakeMimeSniffer()
    ids = SequentialIdGenerator(seed=962)
    factory = InMemoryUnitOfWorkFactory(media)
    gateway = GuestMediaGatewayAdapter(
        request_upload=RequestGuestUploadHandler(
            uow_factory=factory,
            storage=storage,
            source_registrar=FakePlatformSourceRegistrar(),
            clock=CLOCK,
            ids=ids,
        ),
        complete_upload=CompleteUploadHandler(
            uow_factory=factory,
            storage=storage,
            exif_reader=FakeExifReader(),
            mime_sniffer=sniffer,
            task_queue=RecordingTaskQueue(),
            clock=CLOCK,
            ids=ids,
        ),
    )

    grant = await gateway.request_upload(SUBMISSION_ID, "image/png")
    storage.objects[upload_object_key(grant.asset_id)] = StoredObject(
        sha256="a" * 64, byte_size=4096, content_type="image/png"
    )
    sniffer.types[original_object_key(grant.asset_id)] = MimeType.PNG
    completed = await gateway.complete_upload(SUBMISSION_ID, grant.asset_id)

    assert media.media_assets.committed[grant.asset_id].owner_id == SUBMISSION_ID
    assert completed.id == grant.asset_id
    assert completed.mime_type == "image/png"
    assert completed.byte_size == 4096
    assert completed.upload_status is UploadStatus.COMPLETED
