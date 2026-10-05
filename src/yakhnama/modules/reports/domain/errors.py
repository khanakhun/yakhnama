"""Errors of the ``reports`` bounded context.

Every class subclasses a shared-kernel family, so the API maps it to Problem Details by
family without importing this module (``AGENTS.md`` §2.3). Messages are fixed strings;
the report id and status travel in ``details``. Nothing a reporter wrote (description,
reason) or where they were (coordinates) ever appears in an error.

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import Self

from yakhnama.modules.reports.domain.value_objects import ReportStatus
from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    YakhnamaError,
)
from yakhnama.shared_kernel.ids import EntityId


def _details(report_id: EntityId, status: ReportStatus) -> dict[str, object]:
    return {"report_id": str(report_id), "status": status.value}


class ReportNotFoundError(NotFoundError):
    """No report exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, report_id: EntityId) -> Self:
        """Build the error for a missing report id.

        Args:
            report_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such report", details={"report_id": str(report_id)})


class ReportImmutableError(InvalidTransitionError):
    """The report was replaced by a revision and can no longer change.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_report(cls, report_id: EntityId, status: ReportStatus) -> Self:
        """Build the error for a report that is no longer the current revision.

        Args:
            report_id: The report's id.
            status: Its status.

        Returns:
            The error, with id and status in ``details``.
        """
        return cls(
            "the report was superseded; act on its latest revision",
            details=_details(report_id, status),
        )


class ReportAlreadySubmittedError(ConflictError):
    """``submit`` was called on a report that is no longer a draft.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_report(cls, report_id: EntityId, status: ReportStatus) -> Self:
        """Build the error for a report that was submitted before.

        Args:
            report_id: The report's id.
            status: Its status.

        Returns:
            The error, with id and status in ``details``.
        """
        return cls(
            "the report was already submitted", details=_details(report_id, status)
        )


class ReportWithdrawnError(InvalidTransitionError):
    """The report was withdrawn by its reporter and can no longer change.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_report(cls, report_id: EntityId) -> Self:
        """Build the error for a withdrawn report.

        Args:
            report_id: The report's id.

        Returns:
            The error, with id and status in ``details``.
        """
        return cls(
            "the report was withdrawn",
            details=_details(report_id, ReportStatus.WITHDRAWN),
        )


class ReportNotSubmittedError(InvalidTransitionError):
    """The operation needs a submitted report, but the report is still a draft.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_report(cls, report_id: EntityId) -> Self:
        """Build the error for a draft report.

        Args:
            report_id: The report's id.

        Returns:
            The error, with id and status in ``details``.
        """
        return cls(
            "the report is a draft; submit it first",
            details=_details(report_id, ReportStatus.DRAFT),
        )


class ReportRevisionUnchangedError(ValidationError):
    """A revision was requested whose content equals the current revision.

    A revision exists to correct something, so an identical one would only lengthen
    the chain; a retried request is caught by its idempotency key instead.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_report(cls, report_id: EntityId) -> Self:
        """Build the error for an empty revision.

        Args:
            report_id: The id of the report that would be revised.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "the revision changes nothing", details={"report_id": str(report_id)}
        )


class ReportSupersessionMismatchError(InvariantViolationError):
    """A report was to be marked superseded by a report that does not revise it.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_reports(cls, report_id: EntityId, successor_id: EntityId) -> Self:
        """Build the error for a successor that is not the next revision.

        Args:
            report_id: The report that would be superseded.
            successor_id: The report offered as its successor.

        Returns:
            The error, with both ids in ``details``.
        """
        return cls(
            "the successor is not the next revision of this report",
            details={"report_id": str(report_id), "successor_id": str(successor_id)},
        )


# --------------------------------------------------------------------------- #
# Guest submissions (ADR 0020). Each class has its own problem slug, mapped in  #
# ``yakhnama.main``, so a client can tell "start again" from "try later".       #
# --------------------------------------------------------------------------- #


class GuestChallengeInvalidError(ValidationError):
    """The challenge is malformed or its signature is not the platform's.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def create(cls) -> Self:
        """Build the error.

        Returns:
            The error; it says nothing about what exactly failed to verify.
        """
        return cls("the challenge is not valid; request a new one")


class GuestChallengeExpiredError(ValidationError):
    """The challenge was redeemed after it expired.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def create(cls) -> Self:
        """Build the error.

        Returns:
            The error.
        """
        return cls("the challenge has expired; request a new one")


class GuestProofInvalidError(ValidationError):
    """The nonce does not solve the challenge.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def create(cls) -> Self:
        """Build the error.

        Returns:
            The error.
        """
        return cls("the nonce does not solve the challenge")


class GuestChallengeSpentError(ConflictError):
    """The challenge already opened a submission; a challenge is single-use.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def create(cls) -> Self:
        """Build the error.

        Returns:
            The error.
        """
        return cls("the challenge was already used; request a new one")


class GuestCapabilityInvalidError(PermissionDeniedError):
    """No capability, a wrong one, or one for a submission that does not exist.

    The three are not told apart, so submission ids cannot be probed.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def create(cls) -> Self:
        """Build the error.

        Returns:
            The error.
        """
        return cls(
            "the guest capability is missing or not valid for this submission",
            details={"reason": "guest_capability"},
        )


class GuestCapabilityExpiredError(PermissionDeniedError):
    """The capability was right but has expired; the guest must start again.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_submission(cls, submission_id: EntityId) -> Self:
        """Build the error for an expired submission.

        Args:
            submission_id: The submission.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "the guest capability has expired; start a new guest report",
            details={"submission_id": str(submission_id)},
        )


class GuestMediaLimitError(ConflictError):
    """The submission was already granted its maximum number of photos.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_submission(cls, submission_id: EntityId, maximum: int) -> Self:
        """Build the error.

        Args:
            submission_id: The submission.
            maximum: The limit it reached.

        Returns:
            The error, with id and limit in ``details``.
        """
        return cls(
            f"a guest report may carry at most {maximum} photos",
            details={"submission_id": str(submission_id), "max_media": maximum},
        )


class GuestSubmissionClosedError(ConflictError):
    """The submission already carries its report; it accepts nothing else.

    Raised for an upload after the report, and for a second, different report.
    A retry of the same report is not an error: it returns the same receipt.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_submission(cls, submission_id: EntityId) -> Self:
        """Build the error.

        Args:
            submission_id: The submission.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "the guest submission already carries its report",
            details={"submission_id": str(submission_id)},
        )


class GuestSubmissionLimitError(YakhnamaError):
    """Too many guest submissions were opened in the last hour, by all guests.

    The cap protects the moderators' queue when the anonymous path is flooded
    from many addresses at once (ADR 0020). Rendered as 429 ``rate-limited``
    with ``Retry-After`` taken from ``retry_after_seconds``.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        retry_after_seconds: When the oldest submission in the window leaves it.
    """

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        """Create the error.

        Args:
            message: Human-readable explanation, safe for clients.
            retry_after_seconds: Seconds after which a submission may succeed.
        """
        super().__init__(message, details={"retry_after_seconds": retry_after_seconds})
        self.retry_after_seconds = retry_after_seconds

    @classmethod
    def retry_after(cls, seconds: int) -> Self:
        """Build the error.

        Args:
            seconds: Seconds after which a submission may succeed; at least 1.

        Returns:
            The error.
        """
        return cls(
            "too many guest reports are being started; try again later",
            retry_after_seconds=max(1, seconds),
        )


class GuestMediaNotFoundError(NotFoundError):
    """The asset was not granted to this guest submission.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_asset(cls, asset_id: EntityId) -> Self:
        """Build the error.

        Args:
            asset_id: The asset that was asked for.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "no such upload in this guest submission",
            details={"media_id": str(asset_id)},
        )
