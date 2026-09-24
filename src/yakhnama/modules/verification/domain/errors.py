"""Errors raised by the verification domain.

Every class derives from a ``yakhnama.shared_kernel.errors`` family, so the API maps it
to Problem Details by the family's ``code`` (``AGENTS.md`` §2.3). Messages carry only
identifiers and state names, never reason text, which may quote personal details.

A move outside the transition table raises the kernel's ``InvalidTransitionError``
directly, with ``from_state`` and ``to_state`` in its details.

Patterns: Domain Error.
"""

from uuid import UUID

from yakhnama.modules.verification.domain.value_objects import (
    VerificationState,
    VerificationTarget,
)
from yakhnama.shared_kernel.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


class VerificationCaseNotFoundError(NotFoundError):
    """No verification case has the given id.

    Implements: Domain Error.

    Attributes:
        case_id: The id that did not resolve.
    """

    def __init__(self, case_id: UUID) -> None:
        """Create the error.

        Args:
            case_id: The id that did not resolve.
        """
        super().__init__(
            f"verification case {case_id} does not exist",
            details={"case_id": str(case_id)},
        )
        self.case_id = case_id


class ReasonRequiredError(ValidationError):
    """A transition into a state that needs a reason arrived without one.

    Implements: Domain Error.

    Attributes:
        to_state: The requested state.
    """

    def __init__(self, to_state: VerificationState) -> None:
        """Create the error.

        Args:
            to_state: The requested state.
        """
        super().__init__(
            f"moving a case to {to_state.value!r} requires a reason",
            details={"to_state": to_state.value},
        )
        self.to_state = to_state


class HumanRequiredError(PermissionDeniedError):
    """An automated actor tried to make a move only a person may make.

    Implements: Domain Error.

    Attributes:
        to_state: The requested state, ``verified``.
    """

    def __init__(self, to_state: VerificationState) -> None:
        """Create the error.

        Args:
            to_state: The requested state.
        """
        super().__init__(
            f"only a person may move a case to {to_state.value!r}",
            details={"to_state": to_state.value},
        )
        self.to_state = to_state


class CaseAlreadyOpenError(ConflictError):
    """The target already has a verification case; each target has exactly one.

    Implements: Domain Error.

    Attributes:
        target: The target that already has a case.
        case_id: The existing case.
    """

    def __init__(self, target: VerificationTarget, case_id: UUID) -> None:
        """Create the error.

        Args:
            target: The target that already has a case.
            case_id: The existing case.
        """
        super().__init__(
            f"{target.kind.value} {target.target_id} already has verification case "
            f"{case_id}",
            details={
                "target_kind": target.kind.value,
                "target_id": str(target.target_id),
                "case_id": str(case_id),
            },
        )
        self.target = target
        self.case_id = case_id
