"""The media upload flow on a real MinIO: sealing and tamper detection end to end.

The handlers run with in-memory units of work (persistence is tested elsewhere)
and the real storage, EXIF, MIME and scanner adapters, so the tests exercise what
a client can actually do with the presigned URL it was given.
"""

import asyncio
import struct
from dataclasses import dataclass
from io import BytesIO

import httpx
import pytest
from PIL import Image

from tests.fakes.clock import FrozenClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import (
    FakeReportSourceLookup,
    InMemoryMediaQueryService,
    InMemoryMediaUnitOfWork,
)
from tests.fakes.provenance import FakeSourceReferenceMarker, FakeSourceRegistrar
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from tests.integration.conftest import MinioServer
from tests.integration.modules.media.infrastructure.adapters.conftest import (
    STORAGE_NOW,
    Buckets,
    admin_client,
)
from tests.unit.modules.media.infrastructure.adapters.images import image_bytes
from yakhnama.modules.identity.public import Role
from yakhnama.modules.media.infrastructure.adapters.exif import PillowExifReader
from yakhnama.modules.media.infrastructure.adapters.mime import FiletypeMimeSniffer
from yakhnama.modules.media.infrastructure.adapters.s3_storage import S3StoragePort
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    ClamAvScanner,
    tcp_connector,
)
from yakhnama.modules.media.public import (
    CONTENT_CHANGED_QUARANTINE_REASON,
    CompleteUpload,
    CompleteUploadHandler,
    MediaAssetDetail,
    MediaContentChangedError,
    MimeType,
    ModerateMedia,
    ModerateMediaHandler,
    ModerationStatus,
    RecordScanResult,
    RecordScanResultHandler,
    RequestUpload,
    RequestUploadHandler,
    ScanStatus,
    UploadGrant,
    original_object_key,
    upload_object_key,
)
from yakhnama.platform.tasks.handlers import MEDIA_SCAN_TASK
from yakhnama.platform.wiring.media import ScanTaskAdapter
from yakhnama.shared_kernel.tasks import ScheduledTask, TaskId

pytestmark = pytest.mark.integration

IDS = SequentialIdGenerator(seed=911)
OWNER = actor_with(user_id=IDS.new_id())
MODERATOR = actor_with({Role.MODERATOR}, user_id=IDS.new_id())
FIRST_PHOTO = image_bytes("JPEG", size=(4, 2))
REPLACEMENT_PHOTO = image_bytes("JPEG", size=(8, 8))


@dataclass
class Flow:
    """The handlers of one test, sharing one in-memory unit of work."""

    uow: InMemoryMediaUnitOfWork
    storage: S3StoragePort
    request: RequestUploadHandler
    complete: CompleteUploadHandler
    record_scan: RecordScanResultHandler
    moderate: ModerateMediaHandler


@pytest.fixture
def flow(storage: S3StoragePort) -> Flow:
    """Wire the media handlers to the real adapters on the test's buckets."""
    uow = InMemoryMediaUnitOfWork()
    factory = InMemoryUnitOfWorkFactory(uow)
    clock = FrozenClock(STORAGE_NOW)
    ids = SequentialIdGenerator(seed=912)
    registrar = FakeSourceRegistrar()
    return Flow(
        uow=uow,
        storage=storage,
        request=RequestUploadHandler(
            uow_factory=factory,
            storage=storage,
            report_sources=FakeReportSourceLookup({}),
            source_registrar=registrar,
            source_marker=FakeSourceReferenceMarker(registrar),
            clock=clock,
            ids=ids,
        ),
        complete=CompleteUploadHandler(
            uow_factory=factory,
            storage=storage,
            exif_reader=PillowExifReader(storage.read_original),
            mime_sniffer=FiletypeMimeSniffer(storage.read_original_prefix),
            task_queue=RecordingTaskQueue(),
            clock=clock,
            ids=ids,
        ),
        record_scan=RecordScanResultHandler(factory, clock, ids),
        moderate=ModerateMediaHandler(
            uow_factory=factory, storage=storage, clock=clock, ids=ids
        ),
    )


async def _put(grant: UploadGrant, body: bytes) -> httpx.Response:
    headers = {header.name: header.value for header in grant.headers}
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.put(grant.upload_url, content=body, headers=headers)


async def _completed(flow: Flow) -> tuple[UploadGrant, MediaAssetDetail]:
    grant = await flow.request(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))
    assert (await _put(grant, FIRST_PHOTO)).status_code == httpx.codes.OK
    completed = await flow.complete(
        CompleteUpload(actor=OWNER, asset_id=grant.asset_id)
    )
    return grant, completed


def _approve(detail: MediaAssetDetail) -> ModerateMedia:
    return ModerateMedia(
        actor=MODERATOR, asset_id=detail.id, decision=ModerationStatus.APPROVED
    )


async def test_flow_upload_url_never_covers_the_original(flow: Flow) -> None:
    grant = await flow.request(RequestUpload(actor=OWNER, mime_type=MimeType.JPEG))

    assert upload_object_key(grant.asset_id) in grant.upload_url
    assert original_object_key(grant.asset_id) not in grant.upload_url


async def test_flow_overwriting_the_upload_after_completion_publishes_the_first_file(
    flow: Flow,
) -> None:
    grant, completed = await _completed(flow)

    overwrite = await _put(grant, REPLACEMENT_PHOTO)
    await flow.record_scan(
        RecordScanResult(asset_id=completed.id, verdict=ScanStatus.CLEAN)
    )
    published = await flow.moderate(_approve(completed))

    assert overwrite.status_code == httpx.codes.OK
    assert published.is_published
    assert await flow.storage.read_original(original_object_key(completed.id)) == (
        FIRST_PHOTO
    )
    link = await flow.storage.presign_get(f"media/public/{completed.id}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        public = await client.get(link.url)
    assert Image.open(BytesIO(public.content)).size == (4, 2)


async def test_flow_changed_original_refuses_publication_and_quarantines(
    flow: Flow, minio_server: MinioServer, buckets: Buckets
) -> None:
    _, completed = await _completed(flow)
    await flow.record_scan(
        RecordScanResult(asset_id=completed.id, verdict=ScanStatus.CLEAN)
    )
    async with admin_client(minio_server) as client:
        await client.put_object(
            Bucket=buckets.private,
            Key=original_object_key(completed.id),
            Body=REPLACEMENT_PHOTO,
        )

    with pytest.raises(MediaContentChangedError):
        await flow.moderate(_approve(completed))

    stored = flow.uow.media_assets.committed[completed.id]
    assert stored.moderation_status is ModerationStatus.QUARANTINED
    assert stored.moderation_reason == CONTENT_CHANGED_QUARANTINE_REASON
    assert not stored.is_published
    async with admin_client(minio_server) as client:
        listing = await client.list_objects_v2(Bucket=buckets.public)
    assert listing.get("KeyCount", 0) == 0


async def test_flow_scan_of_a_changed_original_quarantines_the_asset(
    flow: Flow, minio_server: MinioServer, buckets: Buckets
) -> None:
    _, completed = await _completed(flow)
    async with admin_client(minio_server) as client:
        await client.put_object(
            Bucket=buckets.private,
            Key=original_object_key(completed.id),
            Body=REPLACEMENT_PHOTO,
        )

    async def fake_clamd(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Would call anything clean; the digest check must stop it first.
        await reader.readexactly(10)
        while (length := struct.unpack(">I", await reader.readexactly(4))[0]) != 0:
            await reader.readexactly(length)
        writer.write(b"stream: OK\x00")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(fake_clamd, "127.0.0.1", 0)
    adapter = ScanTaskAdapter(
        media=InMemoryMediaQueryService(flow.uow),
        scanner=ClamAvScanner(
            connect=tcp_connector("127.0.0.1", server.sockets[0].getsockname()[1]),
            read_chunks=flow.storage.iter_original,
            timeout_seconds=10.0,
        ),
        record_scan_result=flow.record_scan,
    )

    async with server:
        await adapter(
            ScheduledTask.model_validate(
                {
                    "task_id": TaskId(value="scan-1"),
                    "task_name": MEDIA_SCAN_TASK,
                    "payload": {"asset_id": str(completed.id)},
                }
            )
        )

    stored = flow.uow.media_assets.committed[completed.id]
    assert stored.scan_status is ScanStatus.UNAVAILABLE
    assert stored.moderation_status is ModerationStatus.QUARANTINED
