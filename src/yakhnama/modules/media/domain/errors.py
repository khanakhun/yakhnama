"""Errors of the ``media`` bounded context.

Every class subclasses a shared-kernel family, so the API maps it to Problem Details by
family without importing this module (``AGENTS.md`` §2.3). Messages are fixed
strings; the asset id and statuses travel in ``details``. Object keys, digests and
EXIF facts never appear in an error.

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import Self

from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from yakhnama.shared_kernel.ids import EntityId


class MediaAssetNotFoundError(NotFoundError):
    """No media asset exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, asset_id: EntityId) -> Self:
        """Build the error for a missing asset id.

        Args:
            asset_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such media asset", details={"media_id": str(asset_id)})


class MediaUploadNotPendingError(InvalidTransitionError):
    """An upload was completed or failed after it had already ended.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(cls, asset_id: EntityId, upload_status: str) -> Self:
        """Build the error for an upload that is no longer requested.

        Args:
            asset_id: The asset's id.
            upload_status: Its current upload status.

        Returns:
            The error, with id and status in ``details``.
        """
        return cls(
            "the upload is no longer pending",
            details={"media_id": str(asset_id), "upload_status": upload_status},
        )


class MediaUploadNotCompletedError(InvalidTransitionError):
    """The operation needs the file in storage, but the upload has not completed.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(cls, asset_id: EntityId, upload_status: str) -> Self:
        """Build the error for an asset whose file has not arrived.

        Args:
            asset_id: The asset's id.
            upload_status: Its current upload status.

        Returns:
            The error, with id and status in ``details``.
        """
        return cls(
            "the upload has not completed",
            details={"media_id": str(asset_id), "upload_status": upload_status},
        )


class MediaNotPublishableError(InvalidTransitionError):
    """A public copy was to be published before the asset may be shown.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(
        cls, asset_id: EntityId, scan_status: str, moderation_status: str
    ) -> Self:
        """Build the error for an asset that is not cleared for publication.

        Args:
            asset_id: The asset's id.
            scan_status: Its scan status.
            moderation_status: Its moderation status.

        Returns:
            The error, with id and statuses in ``details``.
        """
        return cls(
            "the asset needs a clean scan, a moderator's approval and no blocking "
            "sensitivity before it can be published",
            details={
                "media_id": str(asset_id),
                "scan_status": scan_status,
                "moderation_status": moderation_status,
            },
        )


class InfectedMediaError(InvalidTransitionError):
    """A moderator tried to approve an asset the malware scanner flagged.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(cls, asset_id: EntityId) -> Self:
        """Build the error for an infected asset.

        Args:
            asset_id: The asset's id.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "an infected asset cannot be approved", details={"media_id": str(asset_id)}
        )


class InvalidScanVerdictError(ValidationError):
    """A scan result of ``pending`` was recorded; a scan result is a verdict.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(cls, asset_id: EntityId) -> Self:
        """Build the error for a ``pending`` scan result.

        Args:
            asset_id: The asset's id.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "a scan result must be clean, infected or unavailable",
            details={"media_id": str(asset_id)},
        )


class InvalidModerationDecisionError(ValidationError):
    """A moderation decision was not ``approved`` or ``rejected``, or lacked a reason.

    Quarantine has its own operation, and ``pending`` is not a decision.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(cls, asset_id: EntityId, message: str) -> Self:
        """Build the error for an invalid decision.

        Args:
            asset_id: The asset's id.
            message: Which rule the decision broke; a fixed string.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(message, details={"media_id": str(asset_id)})
