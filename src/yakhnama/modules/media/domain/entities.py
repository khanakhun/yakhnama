"""The ``MediaAsset`` aggregate: one uploaded file, private first, public by decision.

Lifecycle, in the order the application drives it:

1. ``MediaAssetFactory.request_upload`` creates the asset (``upload_status`` =
   ``requested``) and the application hands the client a presigned upload URL for
   ``upload_object_key(id)``, never for ``original_key``.
2. ``complete_upload`` records the digest, size, media type detected from the file's
   magic bytes and EXIF facts read from the private original, which the platform
   copied from the upload key so no client URL can change it afterwards;
   ``fail_upload`` records that the file never arrived or did not match.
3. ``mark_scan`` records the malware scanner's verdict. An ``infected`` verdict also
   quarantines the asset, so an infected file can never stay approved.
4. ``moderate`` records a moderator's approval or rejection and the sensitivity flag;
   ``quarantine`` isolates the asset at any point after completion.
5. ``publish_public_copy`` records the key of the public copy once the scan is clean,
   the asset is approved, no blocking sensitivity is set and its type can be
   stripped of metadata (``PUBLISHABLE_MIME_TYPES``: images only, Q-M13).

**The public copy has no EXIF.** It is a re-encoded file written by the image
transcoder adapter without any metadata block, so the capture time, the camera and
above all the GPS position exist only on the private original. That is a property of
the copy the adapter produces, which the domain records by accepting a
``public_key`` only for the ``public`` variant key (``public_object_key``).

Whenever an asset stops being publishable (rejected, quarantined, infected, or given
a blocking sensitivity) its ``public_key`` is cleared and the event says
``is_public_copy_withdrawn``, so a subscriber deletes the public object.

**Deduplication** (same SHA-256 from the same owner reuses the existing asset) needs a
repository lookup and is an application concern; the domain provides the rule as
``is_duplicate_of``.

Every change validates the whole new state, bumps ``version`` by one, stamps
``updated_at`` from the injected ``Clock`` and draws event ids from the injected
``IdGenerator``. A change already in effect returns the asset unchanged with no
events.

Patterns: Entity, Aggregate Root, Domain Events.
"""

from datetime import UTC, datetime
from typing import Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    TypeAdapter,
    field_validator,
    model_validator,
)

from yakhnama.modules.media.domain.errors import (
    InfectedMediaError,
    InvalidModerationDecisionError,
    InvalidScanVerdictError,
    MediaNotPublishableError,
    MediaUploadNotCompletedError,
    MediaUploadNotPendingError,
)
from yakhnama.modules.media.domain.events import (
    MediaEvent,
    MediaModerated,
    MediaPublished,
    MediaQuarantined,
    MediaScanned,
    UploadCompleted,
    UploadFailed,
)
from yakhnama.modules.media.domain.value_objects import (
    PUBLICATION_BLOCKING_SENSITIVITIES,
    PUBLISHABLE_MIME_TYPES,
    ByteSize,
    ExifFacts,
    MediaVersion,
    MimeType,
    ModerationReason,
    ModerationStatus,
    ObjectKey,
    ScanStatus,
    SensitivityFlag,
    Sha256,
    StoredFile,
    UploadStatus,
    public_object_key,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange, DomainEvent
from yakhnama.shared_kernel.ids import EntityId, IdGenerator

_MODERATION_REASON: TypeAdapter[str] = TypeAdapter(ModerationReason)

INFECTED_QUARANTINE_REASON: Final = "The malware scanner reported an infection."
"""Reason recorded when an infected scan quarantines an asset automatically."""

_DECISIONS: Final = frozenset({ModerationStatus.APPROVED, ModerationStatus.REJECTED})
_REASON_REQUIRED: Final = frozenset(
    {ModerationStatus.REJECTED, ModerationStatus.QUARANTINED}
)


class MediaAsset(BaseModel):
    """One uploaded file with its private original and optional public copy.

    Invariants, checked on every construction:

    - until the upload completes there is no digest, size, EXIF, scan verdict,
      moderation decision or public copy;
    - a completed upload has a digest and a size;
    - a public copy exists only with a clean scan, an approval, no blocking
      sensitivity and a strippable media type, and its key is the asset's
      ``public`` variant key;
    - a rejected or quarantined asset has a moderation reason;
    - ``updated_at`` is never before ``created_at``.

    Implements: Entity / Aggregate Root.

    Attributes:
        id: Stable identity (UUIDv7).
        owner_id: The uploading user.
        report_id: The report the asset belongs to, or ``None``.
        source_id: The ``provenance`` source the asset is attributed to.
        original_key: Storage key of the private original.
        public_key: Storage key of the EXIF-stripped public copy, once published.
        sha256: Digest of the original, once the upload completed.
        mime_type: Declared at request, replaced by the detected type at completion.
        byte_size: Size of the original in bytes, once the upload completed.
        exif: EXIF facts of the original, or ``None`` if it had none.
        upload_status: Whether the file arrived.
        scan_status: The malware scanner's verdict.
        moderation_status: The moderator's decision.
        sensitivity: The moderator's sensitivity flag.
        moderation_reason: Why the asset was rejected, quarantined or approved.
        version: Optimistic-concurrency version.
        created_at: When the asset was requested, UTC.
        updated_at: When the asset last changed, UTC.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EntityId
    owner_id: EntityId
    report_id: EntityId | None = None
    source_id: EntityId
    original_key: ObjectKey
    public_key: ObjectKey | None = None
    sha256: Sha256 | None = None
    mime_type: MimeType
    byte_size: ByteSize | None = None
    exif: ExifFacts | None = None
    upload_status: UploadStatus = UploadStatus.REQUESTED
    scan_status: ScanStatus = ScanStatus.PENDING
    moderation_status: ModerationStatus = ModerationStatus.PENDING
    sensitivity: SensitivityFlag = SensitivityFlag.NONE
    moderation_reason: ModerationReason | None = None
    version: MediaVersion = 1
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if self.upload_status is UploadStatus.COMPLETED:
            if self.sha256 is None or self.byte_size is None:
                message = "a completed upload needs sha256 and byte_size"
                raise ValueError(message)
        elif self._has_post_upload_facts():
            message = "facts and decisions exist only after the upload completed"
            raise ValueError(message)
        if self.public_key is not None and not (
            self.is_publishable and self.public_key == public_object_key(self.id)
        ):
            message = "a public copy needs a clean, approved, publishable asset"
            raise ValueError(message)
        if (
            self.moderation_status in _REASON_REQUIRED
            and self.moderation_reason is None
        ):
            message = "a rejected or quarantined asset needs a moderation reason"
            raise ValueError(message)
        if self.updated_at < self.created_at:
            message = "updated_at must not be earlier than created_at"
            raise ValueError(message)
        return self

    def _has_post_upload_facts(self) -> bool:
        return (
            self.sha256 is not None
            or self.byte_size is not None
            or self.exif is not None
            or self.scan_status is not ScanStatus.PENDING
            or self.moderation_status is not ModerationStatus.PENDING
            or self.public_key is not None
        )

    # ----------------------------------------------------------------------- #
    # Queries                                                                 #
    # ----------------------------------------------------------------------- #

    @property
    def is_publishable(self) -> bool:
        """Tell whether a public copy may exist now.

        Returns:
            ``True`` if the upload completed, the scan is clean, a moderator
            approved, no blocking sensitivity is set and the media type is one
            whose metadata can be stripped (never MP4 or PDF, Q-M13).
        """
        return (
            self.mime_type in PUBLISHABLE_MIME_TYPES
            and self.upload_status is UploadStatus.COMPLETED
            and self.scan_status is ScanStatus.CLEAN
            and self.moderation_status is ModerationStatus.APPROVED
            and self.sensitivity not in PUBLICATION_BLOCKING_SENSITIVITIES
        )

    @property
    def is_published(self) -> bool:
        """Tell whether a public copy exists.

        Returns:
            ``True`` if ``public_key`` is set.
        """
        return self.public_key is not None

    def is_duplicate_of(self, other: "MediaAsset") -> bool:
        """Tell whether ``other`` is the same file from the same owner.

        The application reuses the existing asset instead of storing a second copy.
        Files from different owners are never duplicates, so one user's upload never
        reveals or reuses another user's asset.

        Args:
            other: Another asset.

        Returns:
            ``True`` if both are distinct assets of the same owner with a known,
            equal SHA-256 digest.
        """
        return (
            self.id != other.id
            and self.owner_id == other.owner_id
            and self.sha256 is not None
            and self.sha256 == other.sha256
        )

    # ----------------------------------------------------------------------- #
    # Changes                                                                 #
    # ----------------------------------------------------------------------- #

    def complete_upload(
        self, stored: StoredFile, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["MediaAsset"]:
        """Record that the file arrived and what it is.

        Empty EXIF facts are stored as ``None``, so "no metadata" has one form.

        Args:
            stored: Digest, size, detected media type and EXIF facts of the
                stored original.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The completed asset and ``UploadCompleted``.

        Raises:
            MediaUploadNotPendingError: If the upload already completed or failed.
        """
        self._require_upload_status(UploadStatus.REQUESTED)
        exif = None if stored.exif is None or stored.exif.is_empty else stored.exif
        now = clock.now()
        state = self._evolve(
            now,
            upload_status=UploadStatus.COMPLETED,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=stored.mime_type,
            exif=exif,
        )
        event = state._event(
            UploadCompleted,
            ids,
            now,
            mime_type=stored.mime_type,
            byte_size=stored.byte_size,
            has_exif=exif is not None,
        )
        return AggregateChange[MediaAsset](state=state, events=(event,))

    def fail_upload(
        self, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["MediaAsset"]:
        """Record that the file never arrived or did not match what was declared.

        Args:
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The failed asset and ``UploadFailed``.

        Raises:
            MediaUploadNotPendingError: If the upload already completed or failed.
        """
        self._require_upload_status(UploadStatus.REQUESTED)
        now = clock.now()
        state = self._evolve(now, upload_status=UploadStatus.FAILED)
        return AggregateChange[MediaAsset](
            state=state, events=(state._event(UploadFailed, ids, now),)
        )

    def mark_scan(
        self, status: ScanStatus, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["MediaAsset"]:
        """Record the malware scanner's verdict; ``infected`` also quarantines.

        A later scan may change the verdict (for example after a signature
        update). A ``clean`` verdict never lifts a quarantine: only a moderator
        does, through ``moderate``.

        Args:
            status: ``clean``, ``infected`` or ``unavailable``.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event ids.

        Returns:
            The asset and ``MediaScanned``, followed by ``MediaQuarantined`` if an
            infection quarantined it; or the asset unchanged and no events if the
            verdict is the same.

        Raises:
            InvalidScanVerdictError: If ``status`` is ``pending``.
            MediaUploadNotCompletedError: If the upload has not completed.
        """
        if status is ScanStatus.PENDING:
            raise InvalidScanVerdictError.for_asset(self.id)
        self._require_upload_status(UploadStatus.COMPLETED)
        if status is self.scan_status:
            return AggregateChange[MediaAsset](state=self)
        now = clock.now()
        is_quarantining = (
            status is ScanStatus.INFECTED
            and self.moderation_status is not ModerationStatus.QUARANTINED
        )
        updates: dict[str, object] = {"scan_status": status}
        if is_quarantining:
            updates |= {
                "moderation_status": ModerationStatus.QUARANTINED,
                "moderation_reason": INFECTED_QUARANTINE_REASON,
                "public_key": None,
            }
        state = self._evolve(now, **updates)
        events: list[DomainEvent] = [
            state._event(MediaScanned, ids, now, scan_status=status)
        ]
        if is_quarantining:
            events.append(
                state._event(
                    MediaQuarantined,
                    ids,
                    now,
                    is_public_copy_withdrawn=self.is_published,
                )
            )
        return AggregateChange[MediaAsset](state=state, events=tuple(events))

    def moderate(
        self,
        status: ModerationStatus,
        sensitivity: SensitivityFlag,
        reason: str | None,
        *,
        clock: Clock,
        ids: IdGenerator,
    ) -> AggregateChange["MediaAsset"]:
        """Record a moderator's decision and sensitivity flag.

        Approval may lift a quarantine, because a quarantine waits for exactly this
        decision; it is refused while the scan says ``infected``. A rejection, or a
        sensitivity that blocks publication, withdraws a published public copy.

        Args:
            status: ``approved`` or ``rejected``.
            sensitivity: The sensitivity flag.
            reason: Why; required for a rejection, optional for an approval.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The asset and ``MediaModerated``, or the asset unchanged and no events
            if nothing differs.

        Raises:
            InvalidModerationDecisionError: If ``status`` is not a decision, or a
                rejection has no reason.
            MediaUploadNotCompletedError: If the upload has not completed.
            InfectedMediaError: If an infected asset would be approved.
            pydantic.ValidationError: If ``reason`` is empty, too long or unsafe.
        """
        if status not in _DECISIONS:
            message = "a moderation decision is approved or rejected"
            raise InvalidModerationDecisionError.for_asset(self.id, message)
        if status is ModerationStatus.REJECTED and reason is None:
            message = "a rejection needs a reason"
            raise InvalidModerationDecisionError.for_asset(self.id, message)
        self._require_upload_status(UploadStatus.COMPLETED)
        if status is ModerationStatus.APPROVED and (
            self.scan_status is ScanStatus.INFECTED
        ):
            raise InfectedMediaError.for_asset(self.id)
        normalised = (
            None if reason is None else _MODERATION_REASON.validate_python(reason)
        )
        if (status, sensitivity, normalised) == (
            self.moderation_status,
            self.sensitivity,
            self.moderation_reason,
        ):
            return AggregateChange[MediaAsset](state=self)
        now = clock.now()
        is_withdrawn = self.is_published and (
            status is ModerationStatus.REJECTED
            or sensitivity in PUBLICATION_BLOCKING_SENSITIVITIES
        )
        state = self._evolve(
            now,
            moderation_status=status,
            sensitivity=sensitivity,
            moderation_reason=normalised,
            public_key=None if is_withdrawn else self.public_key,
        )
        event = state._event(
            MediaModerated,
            ids,
            now,
            moderation_status=status,
            sensitivity=sensitivity,
            is_public_copy_withdrawn=is_withdrawn,
        )
        return AggregateChange[MediaAsset](state=state, events=(event,))

    def quarantine(
        self, reason: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["MediaAsset"]:
        """Isolate the asset and withdraw any public copy until a moderator decides.

        Args:
            reason: Why, as safe single-line text of 1 to 500 characters.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The quarantined asset and ``MediaQuarantined``, or the asset unchanged
            and no events if it is already quarantined.

        Raises:
            MediaUploadNotCompletedError: If the upload has not completed.
            pydantic.ValidationError: If ``reason`` is empty, too long or unsafe.
        """
        self._require_upload_status(UploadStatus.COMPLETED)
        if self.moderation_status is ModerationStatus.QUARANTINED:
            return AggregateChange[MediaAsset](state=self)
        normalised = _MODERATION_REASON.validate_python(reason)
        now = clock.now()
        state = self._evolve(
            now,
            moderation_status=ModerationStatus.QUARANTINED,
            moderation_reason=normalised,
            public_key=None,
        )
        event = state._event(
            MediaQuarantined, ids, now, is_public_copy_withdrawn=self.is_published
        )
        return AggregateChange[MediaAsset](state=state, events=(event,))

    def publish_public_copy(
        self, public_key: str, *, clock: Clock, ids: IdGenerator
    ) -> AggregateChange["MediaAsset"]:
        """Record that the EXIF-stripped public copy was written to ``public_key``.

        Args:
            public_key: Where the transcoder wrote the copy; must be
                ``public_object_key(asset.id)``.
            clock: Source of ``updated_at`` and ``occurred_at``.
            ids: Source of the event id.

        Returns:
            The published asset and ``MediaPublished``, or the asset unchanged and
            no events if it is already published at that key.

        Raises:
            MediaNotPublishableError: If the scan is not clean, the asset is not
                approved, or a blocking sensitivity is set.
            pydantic.ValidationError: If ``public_key`` is not the public variant
                key of this asset.
        """
        if not self.is_publishable:
            raise MediaNotPublishableError.for_asset(
                self.id, self.scan_status.value, self.moderation_status.value
            )
        if public_key == self.public_key:
            return AggregateChange[MediaAsset](state=self)
        now = clock.now()
        state = self._evolve(now, public_key=public_key)
        return AggregateChange[MediaAsset](
            state=state, events=(state._event(MediaPublished, ids, now),)
        )

    # ----------------------------------------------------------------------- #
    # Internals                                                               #
    # ----------------------------------------------------------------------- #

    def _require_upload_status(self, expected: UploadStatus) -> None:
        if self.upload_status is expected:
            return
        if expected is UploadStatus.REQUESTED:
            raise MediaUploadNotPendingError.for_asset(
                self.id, self.upload_status.value
            )
        raise MediaUploadNotCompletedError.for_asset(self.id, self.upload_status.value)

    def _evolve(self, now: datetime, **updates: object) -> "MediaAsset":
        # model_validate, not model_copy: model_copy skips validation and would let a
        # change break an invariant.
        fields = {name: getattr(self, name) for name in type(self).model_fields}
        return self.model_validate(
            {**fields, **updates, "version": self.version + 1, "updated_at": now}
        )

    def _event[EventT: MediaEvent](
        self,
        event_class: type[EventT],
        ids: IdGenerator,
        now: datetime,
        **fields: object,
    ) -> EventT:
        return event_class.model_validate(
            {
                "event_id": ids.new_id(),
                "occurred_at": now,
                "aggregate_id": self.id,
                "version": self.version,
                **fields,
            }
        )
