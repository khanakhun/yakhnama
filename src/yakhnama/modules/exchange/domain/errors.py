"""Errors of the ``exchange`` bounded context.

Every class subclasses a shared-kernel family, so the API maps it to Problem Details
by family without importing this module (``AGENTS.md`` §2.3). Messages are fixed
strings; ids, statuses and codes travel in ``details``. File contents and row values
never appear in an error.

Patterns: Domain Error (proposed in ADR 0012).
"""

from collections.abc import Sequence
from typing import Final, Literal, Self

from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from yakhnama.shared_kernel.ids import EntityId

JobKind = Literal["export_job", "import_job"]
"""Which kind of job an error is about."""

_MAX_REPORTED_CODE_LENGTH: Final = 32
_MAX_LISTED_COLUMNS: Final = 20


class ExportJobNotFoundError(NotFoundError):
    """No export job exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, job_id: EntityId) -> Self:
        """Build the error for a missing export job.

        Args:
            job_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such export job", details={"export_job_id": str(job_id)})


class ImportJobNotFoundError(NotFoundError):
    """No import job exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, job_id: EntityId) -> Self:
        """Build the error for a missing import job.

        Args:
            job_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such import job", details={"import_job_id": str(job_id)})


class JobStateError(InvalidTransitionError):
    """A job was asked to change in a way its status does not allow.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_job(cls, kind: JobKind, job_id: EntityId, status: str, action: str) -> Self:
        """Build the error for a refused job transition.

        Args:
            kind: ``export_job`` or ``import_job``.
            job_id: The job's id.
            status: Its current status.
            action: What was asked, for example ``"start"``.

        Returns:
            The error, with kind, id, status and action in ``details``.
        """
        return cls(
            "the job's status does not allow this change",
            details={
                "job_kind": kind,
                "job_id": str(job_id),
                "status": status,
                "action": action,
            },
        )


class UnsupportedFormatError(ValidationError):
    """An export or import was asked for in a format that is not registered.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_code(cls, code: str, direction: str) -> Self:
        """Build the error for an unknown format code.

        Args:
            code: The requested code; cut to 32 characters in ``details``.
            direction: ``export`` or ``import``.

        Returns:
            The error, with code and direction in ``details``.
        """
        return cls(
            "the format is not supported",
            details={
                "format": code[:_MAX_REPORTED_CODE_LENGTH],
                "direction": direction,
            },
        )


class ImportContractError(ValidationError):
    """An import file does not follow the documented layout as a whole.

    Raised for the header or structure of a file (missing, unknown or repeated
    columns, too many rows); problems of single rows go into the validation report
    instead.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_header(
        cls,
        missing: Sequence[str],
        unknown_count: int,
        duplicated: Sequence[str],
    ) -> Self:
        """Build the error for a header that breaks the column contract.

        Unknown column names are counted, not listed, because they are untrusted
        text from the file.

        Args:
            missing: Required contract columns that are absent.
            unknown_count: How many header names are not contract columns.
            duplicated: Contract columns that appear more than once.

        Returns:
            The error, with the problems in ``details``.
        """
        return cls(
            "the file header does not follow the import column contract",
            details={
                "missing_columns": tuple(missing[:_MAX_LISTED_COLUMNS]),
                "unknown_column_count": unknown_count,
                "duplicated_columns": tuple(duplicated[:_MAX_LISTED_COLUMNS]),
            },
        )

    @classmethod
    def too_many_rows(cls, limit: int) -> Self:
        """Build the error for a file with more data rows than allowed.

        Args:
            limit: The maximum number of data rows.

        Returns:
            The error, with the limit in ``details``.
        """
        return cls(
            "the file has more data rows than an import accepts",
            details={"max_rows": limit},
        )
