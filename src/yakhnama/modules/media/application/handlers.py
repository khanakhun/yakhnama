"""Write-side use cases of the media module.

The upload flow, in the order a client drives it:

1. ``RequestUploadHandler`` creates the asset (``requested``) and returns a
   presigned ``PUT`` for the private original. Its source is the report's source
   when the file belongs to the uploader's report, otherwise a new ``citizen``
   source registered through the provenance facade.
2. ``CompleteUploadHandler`` checks with storage that the file arrived, detects its
   media type from its magic bytes, reads its EXIF and completes the asset, then
   enqueues the ``media.scan`` task. **Deduplication:** if the uploader already has
   a completed asset with the same SHA-256 (``MediaAsset.is_duplicate_of``), the new
   asset is marked ``failed`` and the existing asset is returned instead, so one
   file is stored, scanned and moderated once. The duplicate original stays in the
   private bucket until a storage clean-up removes it (open question).
3. ``RecordScanResultHandler`` stores the scanner's verdict (system task).
4. ``ModerateMediaHandler`` records the moderator's decision; when the asset is
   then publishable (clean, approved, no blocking sensitivity) it writes the
   EXIF-stripped public copy and records it. The decision is committed first and
   the copy published in a second unit of work, so a failed copy never loses the
   decision and repeating the command completes the publication.

Storage, EXIF and MIME adapters do network I/O; they are called outside a unit of
work where the result does not decide what is staged, and before staging where it
does.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from datetime import datetime

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.application.authorisation import (
    moderation_policy,
    require_allowed,
    uploader_policy,
)
from yakhnama.modules.media.application.commands import (
    CompleteUpload,
    ModerateMedia,
    RecordScanResult,
    RequestUpload,
)
from yakhnama.modules.media.application.dto import MediaAssetDetail, UploadGrant
from yakhnama.modules.media.application.ports import (
    SCAN_TASK,
    ExifReader,
    MediaUnitOfWork,
    MediaUnitOfWorkFactory,
    MimeSniffer,
    ReportSourceLookup,
    StoragePort,
)
from yakhnama.modules.media.domain.entities import MediaAsset
from yakhnama.modules.media.domain.errors import MediaAssetNotFoundError
from yakhnama.modules.media.domain.factories import MediaAssetFactory
from yakhnama.modules.media.domain.value_objects import (
    MAX_MEDIA_BYTES,
    MediaAttribution,
    StoredFile,
    UploadStatus,
    public_object_key,
)
from yakhnama.modules.provenance.public import (
    MarkSourceReferenced,
    RegisterSource,
    SourceDetails,
    SourceReferenceMarker,
    SourceRegistrar,
    SourceType,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import (
    PermissionDeniedError,
    ValidationError,
)
from yakhnama.shared_kernel.ids import EntityId, IdGenerator
from yakhnama.shared_kernel.tasks import TaskQueue

# Platform-written text for the source of a file uploaded without a report
# (**proposed**); it never names the uploader.
UPLOAD_SOURCE_TITLE = "Community media upload"


def _upload_source_details(now: datetime) -> SourceDetails:
    return SourceDetails(
        title=UPLOAD_SOURCE_TITLE,
        citation=f"Yakhnama community media upload, {now:%Y-%m-%d}",
    )


def _require_user(actor: Actor, *, action: str) -> EntityId:
    # The rule and error of require_allowed(IsAuthenticated(), ...), with the user
    # id returned so it is known to be set from here on.
    if actor.user_id is None:
        message = f"the actor may not {action}"
        raise PermissionDeniedError(
            message, details={"action": action, "policy": "IsAuthenticated"}
        )
    return actor.user_id


async def _load_asset(uow: MediaUnitOfWork, asset_id: EntityId) -> MediaAsset:
    asset = await uow.media_assets.get(asset_id)
    if asset is None:
        raise MediaAssetNotFoundError.for_id(asset_id)
    return asset


class RequestUploadHandler:
    """Create an asset and grant a presigned upload of its original.

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: MediaUnitOfWorkFactory,
        storage: StoragePort,
        report_sources: ReportSourceLookup,
        source_registrar: SourceRegistrar,
        source_marker: SourceReferenceMarker,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a media unit of work per call.
            storage: Presigns the upload.
            report_sources: Finds the source of the uploader's report.
            source_registrar: Registers a source for a file without a report.
            source_marker: Marks that source referenced.
            clock: Source of timestamps and event times.
            ids: Source of asset and event ids.
        """
        self._uow_factory = uow_factory
        self._storage = storage
        self._report_sources = report_sources
        self._source_registrar = source_registrar
        self._source_marker = source_marker
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RequestUpload) -> UploadGrant:
        """Create the asset and presign its upload.

        Args:
            command: The validated command.

        Returns:
            The asset id, upload URL, headers, expiry and size limit.

        Raises:
            PermissionDeniedError: If the actor is anonymous, or the report does
                not exist or is someone else's (not distinguished, so report ids
                cannot be probed).
        """
        owner_id = _require_user(command.actor, action="upload media")
        is_new_source = command.report_id is None
        source_id = await self._resolve_source(command, owner_id)
        attribution = MediaAttribution(
            owner_id=owner_id, source_id=source_id, report_id=command.report_id
        )
        async with self._uow_factory() as uow:
            asset = (
                MediaAssetFactory()
                .request_upload(
                    attribution, command.mime_type, clock=self._clock, ids=self._ids
                )
                .record_into(uow)
            )
            await uow.media_assets.add(asset)
            await uow.commit()
        if is_new_source:
            await self._source_marker(
                MarkSourceReferenced(actor=command.actor, source_id=source_id)
            )
        upload = await self._storage.presign_put(
            asset.original_key, command.mime_type, MAX_MEDIA_BYTES
        )
        return UploadGrant(
            asset_id=asset.id,
            upload_url=upload.url,
            headers=upload.headers,
            expires_at=upload.expires_at,
            max_bytes=MAX_MEDIA_BYTES,
        )

    async def _resolve_source(
        self, command: RequestUpload, owner_id: EntityId
    ) -> EntityId:
        if command.report_id is not None:
            source_id = await self._report_sources.find_source_for_reporter(
                command.report_id, owner_id
            )
            if source_id is None:
                message = "the actor may attach media only to their own reports"
                raise PermissionDeniedError(
                    message, details={"action": "upload media", "reason": "report"}
                )
            return source_id
        source = await self._source_registrar(
            RegisterSource(
                actor=command.actor,
                source_type=SourceType.CITIZEN,
                details=_upload_source_details(self._clock.now()),
            )
        )
        return source.id


class CompleteUploadHandler:
    """Verify an uploaded original, complete its asset and schedule its scan.

    Implements: Command Handler.
    """

    def __init__(  # noqa: PLR0913  # reason: one keyword per injected port, all required
        self,
        *,
        uow_factory: MediaUnitOfWorkFactory,
        storage: StoragePort,
        exif_reader: ExifReader,
        mime_sniffer: MimeSniffer,
        task_queue: TaskQueue,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a media unit of work per call.
            storage: Confirms the upload and reports its digest and size.
            exif_reader: Reads the original's EXIF facts.
            mime_sniffer: Detects the original's media type.
            task_queue: Schedules the malware scan.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._storage = storage
        self._exif_reader = exif_reader
        self._mime_sniffer = mime_sniffer
        self._task_queue = task_queue
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: CompleteUpload) -> MediaAssetDetail:
        """Complete the upload, or return the uploader's existing identical asset.

        Args:
            command: The validated command.

        Returns:
            The completed asset; for a duplicate, the earlier asset with the same
            content. Repeating the call for a completed asset returns it again.

        Raises:
            PermissionDeniedError: If the actor is not the uploader.
            MediaAssetNotFoundError: If the asset does not exist.
            ValidationError: If the file has not arrived (nothing changes, so the
                client may retry), or is empty, too large or not an allowed media
                type (the asset is marked ``failed``).
            MediaUploadNotPendingError: If the upload already failed.
        """
        rejection: ValidationError | None = None
        existing: MediaAsset | None = None
        async with self._uow_factory() as uow:
            asset = await _load_asset(uow, command.asset_id)
            require_allowed(
                uploader_policy(asset.owner_id),
                command.actor,
                action="complete this upload",
            )
            if asset.upload_status is UploadStatus.COMPLETED:
                return MediaAssetDetail.from_entity(asset)
            stored = await self._stored_file(asset)
            if isinstance(stored, ValidationError):
                rejection = stored
                change = asset.fail_upload(clock=self._clock, ids=self._ids)
            else:
                change = asset.complete_upload(stored, clock=self._clock, ids=self._ids)
                existing = await uow.media_assets.find_completed_by_digest(
                    asset.owner_id, stored.sha256
                )
                if existing is not None and change.state.is_duplicate_of(existing):
                    change = asset.fail_upload(clock=self._clock, ids=self._ids)
                else:
                    existing = None
            asset = change.record_into(uow)
            await uow.media_assets.save(asset)
            await uow.commit()
        if rejection is not None:
            raise rejection
        if existing is not None:
            return MediaAssetDetail.from_entity(existing)
        await self._task_queue.enqueue(
            SCAN_TASK,
            {"asset_id": asset.id},
            idempotency_key=f"{SCAN_TASK}:{asset.id}",
        )
        return MediaAssetDetail.from_entity(asset)

    async def _stored_file(self, asset: MediaAsset) -> StoredFile | ValidationError:
        key = asset.original_key
        stored = await self._storage.head(key)
        if stored is None:
            # Nothing is staged: the client may still be uploading.
            message = "the file has not been uploaded yet"
            raise ValidationError(message, details={"reason": "object_missing"})
        if not 1 <= stored.byte_size <= MAX_MEDIA_BYTES:
            return ValidationError(
                "the file is empty or larger than allowed",
                details={"reason": "size", "max_bytes": MAX_MEDIA_BYTES},
            )
        mime_type = await self._mime_sniffer.sniff(key)
        if mime_type is None:
            return ValidationError(
                "the file is not an allowed media type",
                details={"reason": "mime_type"},
            )
        return StoredFile(
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=mime_type,
            exif=await self._exif_reader.read(key),
        )


class RecordScanResultHandler:
    """Store the malware scanner's verdict; a system task, no actor.

    It is never routed from the API. An ``infected`` verdict quarantines the asset
    in the domain. A repeated verdict commits no event.

    Implements: Command Handler.
    """

    def __init__(
        self, uow_factory: MediaUnitOfWorkFactory, clock: Clock, ids: IdGenerator
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a media unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: RecordScanResult) -> MediaAssetDetail:
        """Record the verdict.

        Args:
            command: The asset and verdict.

        Returns:
            The asset after the change.

        Raises:
            MediaAssetNotFoundError: If the asset does not exist.
            InvalidScanVerdictError: If the verdict is ``pending``.
            MediaUploadNotCompletedError: If the upload has not completed.
        """
        async with self._uow_factory() as uow:
            asset = await _load_asset(uow, command.asset_id)
            change = asset.mark_scan(command.verdict, clock=self._clock, ids=self._ids)
            if change.events:
                asset = change.record_into(uow)
                await uow.media_assets.save(asset)
            await uow.commit()
        return MediaAssetDetail.from_entity(asset)


class ModerateMediaHandler:
    """Record a moderator's decision and publish the public copy when allowed.

    Implements: Command Handler.
    """

    def __init__(
        self,
        *,
        uow_factory: MediaUnitOfWorkFactory,
        storage: StoragePort,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a media unit of work per step.
            storage: Writes the EXIF-stripped public copy.
            clock: Source of timestamps and event times.
            ids: Source of event ids.
        """
        self._uow_factory = uow_factory
        self._storage = storage
        self._clock = clock
        self._ids = ids

    async def __call__(self, command: ModerateMedia) -> MediaAssetDetail:
        """Moderate the asset, then publish it if it became publishable.

        Args:
            command: The validated command.

        Returns:
            The asset after the decision and any publication.

        Raises:
            PermissionDeniedError: If the actor may not moderate.
            MediaAssetNotFoundError: If the asset does not exist.
            InvalidModerationDecisionError: If the decision is not a decision, or a
                rejection has no reason.
            MediaUploadNotCompletedError: If the upload has not completed.
            InfectedMediaError: If an infected asset would be approved.
        """
        require_allowed(moderation_policy(), command.actor, action="moderate media")
        async with self._uow_factory() as uow:
            asset = await _load_asset(uow, command.asset_id)
            change = asset.moderate(
                command.decision,
                command.sensitivity,
                command.reason,
                clock=self._clock,
                ids=self._ids,
            )
            if change.events:
                asset = change.record_into(uow)
                await uow.media_assets.save(asset)
            await uow.commit()
        if asset.is_publishable and not asset.is_published:
            asset = await self._publish(asset)
        return MediaAssetDetail.from_entity(asset)

    async def _publish(self, asset: MediaAsset) -> MediaAsset:
        public_key = public_object_key(asset.id)
        # The copy is written before the record so a published asset always has
        # an object behind it; a failure here leaves the approval committed.
        await self._storage.copy_stripped_public(asset.original_key, public_key)
        async with self._uow_factory() as uow:
            current = await _load_asset(uow, asset.id)
            change = current.publish_public_copy(
                public_key, clock=self._clock, ids=self._ids
            )
            published = change.record_into(uow)
            if change.events:
                await uow.media_assets.save(published)
            await uow.commit()
        return published
