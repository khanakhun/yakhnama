"""Write requests accepted by the media command handlers.

Patterns: Command.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from yakhnama.modules.identity.public import Actor
from yakhnama.modules.media.domain.value_objects import (
    MimeType,
    ModerationReason,
    ModerationStatus,
    ScanStatus,
    SensitivityFlag,
)
from yakhnama.shared_kernel.ids import EntityId


class RequestUpload(BaseModel):
    """Ask for a presigned URL to upload one file.

    Implements: Command.

    Attributes:
        actor: The uploader.
        report_id: The uploader's report the file belongs to, or ``None`` for a
            file uploaded before its report is submitted.
        mime_type: The media type the uploader declares; checked again against
            the file's content at completion.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    report_id: EntityId | None = None
    mime_type: MimeType


class CompleteUpload(BaseModel):
    """Tell the platform the file behind an asset has been uploaded.

    Implements: Command.

    Attributes:
        actor: The uploader.
        asset_id: The asset the upload URL was granted for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    asset_id: EntityId


class RecordScanResult(BaseModel):
    """Store the malware scanner's verdict on an asset's original.

    Internal: sent by the ``media.scan`` task; no endpoint accepts it and it
    carries no actor.

    Implements: Command.

    Attributes:
        asset_id: The scanned asset.
        verdict: ``clean``, ``infected`` or ``unavailable``.
        is_content_changed: The bytes the scanner read no longer hash to the
            recorded digest; the asset is quarantined and the verdict must be
            ``unavailable``, since it describes some other file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: EntityId
    verdict: ScanStatus
    is_content_changed: bool = False

    @model_validator(mode="after")
    def _changed_content_has_no_verdict(self) -> Self:
        # A verdict on bytes that are not the recorded file must never read as
        # "clean" or "infected" for it.
        if self.is_content_changed and self.verdict is not ScanStatus.UNAVAILABLE:
            message = "a changed original can only have the verdict 'unavailable'"
            raise ValueError(message)
        return self


class ModerateMedia(BaseModel):
    """Record a moderator's decision on an asset, publishing it when allowed.

    Implements: Command.

    Attributes:
        actor: The moderator.
        asset_id: The asset.
        decision: ``approved`` or ``rejected``.
        sensitivity: The sensitivity flag.
        reason: Why; required for a rejection.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: Actor
    asset_id: EntityId
    decision: ModerationStatus
    sensitivity: SensitivityFlag = SensitivityFlag.NONE
    reason: ModerationReason | None = None
