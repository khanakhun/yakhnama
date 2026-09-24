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
    ValidationError,
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
