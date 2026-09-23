"""Shared arrangements for the media application tests."""

from datetime import UTC, datetime

from tests.factories.media import synthetic_sha256
from tests.fakes.clock import FrozenClock
from tests.fakes.identity import actor_with
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.media import (
    FakeExifReader,
    FakeMimeSniffer,
    FakeReportSourceLookup,
    FakeStoragePort,
    InMemoryMediaQueryService,
    InMemoryMediaUnitOfWork,
)
from tests.fakes.provenance import FakeSourceReferenceMarker, FakeSourceRegistrar
from tests.fakes.tasks import RecordingTaskQueue
from tests.fakes.uow import InMemoryUnitOfWorkFactory
from yakhnama.modules.identity.public import Role
from yakhnama.modules.media.application.commands import (
    CompleteUpload,
    ModerateMedia,
    RecordScanResult,
    RequestUpload,
)
from yakhnama.modules.media.application.dto import MediaAssetDetail, StoredObject
from yakhnama.modules.media.application.handlers import (
    CompleteUploadHandler,
    ModerateMediaHandler,
    RecordScanResultHandler,
    RequestUploadHandler,
)
from yakhnama.modules.media.application.query_services import (
    AuthorisedMediaQueryService,
)
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.value_objects import (
    ExifFacts,
    MimeType,
    ModerationStatus,
    ScanStatus,
    original_object_key,
    upload_object_key,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.value_objects import Coordinates

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
IDS = SequentialIdGenerator(seed=601)
OWNER_ID = IDS.new_id()
OTHER_ID = IDS.new_id()
REPORT_ID = IDS.new_id()
REPORT_SOURCE_ID = IDS.new_id()
MISSING_ID = IDS.new_id()
OWNER = actor_with(user_id=OWNER_ID)
OTHER_CITIZEN = actor_with(user_id=OTHER_ID)
MODERATOR = actor_with({Role.MODERATOR}, user_id=OTHER_ID)
EXIF = ExifFacts(location=Coordinates(longitude=74.3, latitude=35.9), camera="Cam X")


class Harness:
    """Fakes and handlers of one media test, sharing one unit of work."""

    def __init__(self, *assets: MediaAsset) -> None:
        """Arrange the fakes around ``assets``."""
        self.uow = InMemoryMediaUnitOfWork(assets=assets)
        self.factory = InMemoryUnitOfWorkFactory(self.uow)
        self.storage = FakeStoragePort()
        self.exif = FakeExifReader()
        self.sniffer = FakeMimeSniffer()
        self.tasks = RecordingTaskQueue()
        self.reports = FakeReportSourceLookup({REPORT_ID: (OWNER_ID, REPORT_SOURCE_ID)})
        self.registrar = FakeSourceRegistrar()
        self.marker = FakeSourceReferenceMarker(self.registrar)
        self.clock = FrozenClock(NOW)
        self.ids = SequentialIdGenerator(seed=602)

    def request(self) -> RequestUploadHandler:
        """Return the request handler."""
        return RequestUploadHandler(
            uow_factory=self.factory,
            storage=self.storage,
            report_sources=self.reports,
            source_registrar=self.registrar,
            source_marker=self.marker,
            clock=self.clock,
            ids=self.ids,
        )

    def complete(self) -> CompleteUploadHandler:
        """Return the complete handler."""
        return CompleteUploadHandler(
            uow_factory=self.factory,
            storage=self.storage,
            exif_reader=self.exif,
            mime_sniffer=self.sniffer,
            task_queue=self.tasks,
            clock=self.clock,
            ids=self.ids,
        )

    def scan(self) -> RecordScanResultHandler:
        """Return the scan-result handler."""
        return RecordScanResultHandler(self.factory, self.clock, self.ids)

    def moderate(self) -> ModerateMediaHandler:
        """Return the moderation handler."""
        return ModerateMediaHandler(
            uow_factory=self.factory,
            storage=self.storage,
            clock=self.clock,
            ids=self.ids,
        )

    def queries(self) -> AuthorisedMediaQueryService:
        """Return the authorised query service."""
        return AuthorisedMediaQueryService(
            InMemoryMediaQueryService(self.uow), self.storage
        )

    def upload(self, asset_id: EntityId, sha256: str | None = None) -> str:
        """Upload a JPEG with EXIF to the asset's upload key; return its digest.

        The sniffer and EXIF reader answer for the original key, the only key
        read after completion seals the upload there.
        """
        digest = synthetic_sha256() if sha256 is None else sha256
        self.storage.objects[upload_object_key(asset_id)] = StoredObject(
            sha256=digest, byte_size=2048, content_type="image/jpeg"
        )
        key = original_object_key(asset_id)
        self.sniffer.types[key] = MimeType.JPEG
        self.exif.facts[key] = EXIF
        return digest

    async def uploaded(
        self, actor_id: EntityId = OWNER_ID, sha256: str | None = None
    ) -> MediaAssetDetail:
        """Request, upload and complete one asset owned by ``actor_id``."""
        actor = OWNER if actor_id == OWNER_ID else OTHER_CITIZEN
        grant = await self.request()(
            RequestUpload(actor=actor, mime_type=MimeType.JPEG)
        )
        self.upload(grant.asset_id, sha256)
        return await self.complete()(
            CompleteUpload(actor=actor, asset_id=grant.asset_id)
        )

    async def published(self) -> MediaAssetDetail:
        """Run the whole flow to a published asset owned by ``OWNER_ID``."""
        completed = await self.uploaded()
        await self.scan()(
            RecordScanResult(asset_id=completed.id, verdict=ScanStatus.CLEAN)
        )
        return await self.moderate()(
            ModerateMedia(
                actor=MODERATOR,
                asset_id=completed.id,
                decision=ModerationStatus.APPROVED,
            )
        )
