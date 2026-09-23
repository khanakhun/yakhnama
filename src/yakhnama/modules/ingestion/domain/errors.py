"""Errors of the ``ingestion`` bounded context.

Every class subclasses a shared-kernel family, so the API maps it to Problem Details
by family without importing this module (``AGENTS.md`` §2.3). Messages are fixed
strings; ``details`` carries only ids, codes, status names and unit names, never a
payload value, because ingested rows come from third parties and are untrusted.

Patterns: Domain Error (proposed in ADR 0012).
"""

import re
from typing import Final, Self

from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)
from yakhnama.shared_kernel.ids import EntityId

# Variable codes and unit names reach these errors straight from a pipeline, so they
# are repeated only when they look like identifiers; anything else could be
# third-party text and is replaced by a marker.
_IDENTIFIER: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
UNPRINTABLE_VALUE: Final = "<not an identifier>"


def _identifier(value: str) -> str:
    return value if _IDENTIFIER.fullmatch(value) else UNPRINTABLE_VALUE


class DatasetNotFoundError(NotFoundError):
    """No dataset exists with the requested id or code.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, dataset_id: EntityId) -> Self:
        """Build the error for a missing dataset id.

        Args:
            dataset_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such dataset", details={"dataset_id": str(dataset_id)})

    @classmethod
    def for_code(cls, code: str) -> Self:
        """Build the error for a missing dataset code.

        Args:
            code: The ``DatasetCode`` that was looked up.

        Returns:
            The error, with the code in ``details``.
        """
        return cls("no such dataset", details={"code": code})


class DatasetVersionNotFoundError(NotFoundError):
    """No dataset version exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, dataset_version_id: EntityId) -> Self:
        """Build the error for a missing dataset version id.

        Args:
            dataset_version_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "no such dataset version",
            details={"dataset_version_id": str(dataset_version_id)},
        )


class IngestionRunNotFoundError(NotFoundError):
    """No ingestion run exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, run_id: EntityId) -> Self:
        """Build the error for a missing run id.

        Args:
            run_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such ingestion run", details={"run_id": str(run_id)})


class LicenceRequiredError(InvariantViolationError):
    """A dataset was registered without a licence.

    Nothing is ever ingested without a recorded licence (Phase 4 plan §1): the terms
    decide whether Yakhnama may store, derive from and republish the data at all.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_code(cls, code: str) -> Self:
        """Build the error for a dataset code registered without a licence.

        Args:
            code: The ``DatasetCode`` of the refused dataset.

        Returns:
            The error, with the code in ``details``.
        """
        return cls(
            "a dataset needs a recorded licence before anything is ingested",
            details={"code": code},
        )


class DatasetStatusError(InvalidTransitionError):
    """A dataset was asked to do something its lifecycle status does not allow.

    Raised for a status move outside the dataset transition table and for work on a
    dataset that no longer accepts it (a new version or run of a retired dataset).

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def refused(cls, dataset_id: EntityId, status: str, action: str) -> Self:
        """Build the error for an action refused in the current status.

        Args:
            dataset_id: Id of the dataset.
            status: Its current ``DatasetStatus`` value.
            action: What was attempted, a fixed snake-case word such as
                ``"deprecate"``.

        Returns:
            The error, with id, status and action in ``details``.
        """
        return cls(
            "the dataset's status does not allow this action",
            details={
                "dataset_id": str(dataset_id),
                "status": status,
                "action": action,
            },
        )


class RunStateError(InvalidTransitionError):
    """An ingestion run was asked to make a move its transition table forbids.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_move(cls, run_id: EntityId, from_status: str, to_status: str) -> Self:
        """Build the error for a forbidden run transition.

        Args:
            run_id: Id of the run.
            from_status: Its current ``RunStatus`` value.
            to_status: The requested ``RunStatus`` value.

        Returns:
            The error, with id and both statuses in ``details``.
        """
        return cls(
            "the ingestion run cannot move to the requested status",
            details={
                "run_id": str(run_id),
                "from_status": from_status,
                "to_status": to_status,
            },
        )


class RunOutcomeError(InvariantViolationError):
    """A run's outcome does not match the report and counts it was finished with.

    For example a ``succeeded`` run whose report lists errors, or counts that differ
    from the counts inside the report.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def because(cls, run_id: EntityId, reason: str) -> Self:
        """Build the error with the rule that failed.

        Args:
            run_id: Id of the run.
            reason: The failed rule, a fixed sentence; never a report message.

        Returns:
            The error, with id and reason in ``details``.
        """
        return cls(
            "the run outcome is inconsistent with its report",
            details={"run_id": str(run_id), "reason": reason},
        )


class UnknownVariableError(ValidationError):
    """An observation names a variable code that is not in the registry.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_code(cls, variable: str) -> Self:
        """Build the error for an unregistered variable code.

        Args:
            variable: The code that was not found; repeated only if it looks
                like an identifier.

        Returns:
            The error, with the code in ``details``.
        """
        return cls(
            "unknown observation variable", details={"variable": _identifier(variable)}
        )


class ObservationUnitMismatchError(ValidationError):
    """An observation's unit differs from the unit its variable is stored in.

    Conversion to the registry unit belongs to the pipeline's ``normalise`` step; the
    domain refuses a value that was not converted rather than guessing.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_units(cls, variable: str, expected_unit: str, actual_unit: str) -> Self:
        """Build the error for a unit that does not match the registry.

        Args:
            variable: The observation's variable code.
            expected_unit: The unit the registry stores the variable in.
            actual_unit: The unit the observation carried.

        Returns:
            The error, with the variable and both units in ``details``.
        """
        return cls(
            "the observation's unit is not the unit its variable is stored in",
            details={
                "variable": _identifier(variable),
                "expected_unit": expected_unit,
                "actual_unit": _identifier(actual_unit),
            },
        )


class VersionDatasetMismatchError(InvariantViolationError):
    """A dataset version was used with a dataset it does not belong to.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_ids(cls, dataset_id: EntityId, dataset_version_id: EntityId) -> Self:
        """Build the error for a version paired with the wrong dataset.

        Args:
            dataset_id: The dataset given.
            dataset_version_id: The version given, which belongs to another dataset.

        Returns:
            The error, with both ids in ``details``.
        """
        return cls(
            "the dataset version does not belong to the dataset",
            details={
                "dataset_id": str(dataset_id),
                "dataset_version_id": str(dataset_version_id),
            },
        )
