"""Errors raised by the hazards domain.

Every class derives from a ``yakhnama.shared_kernel.errors`` family, so the API maps it
to Problem Details by the family's ``code`` without importing this module
(``AGENTS.md`` §2.3). Messages name only hazard codes, which are public reference data.

Patterns: Domain Error.
"""

from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
    ValidationError,
)


class HazardTypeNotFoundError(NotFoundError):
    """No hazard type, active or retired, has the given code.

    Implements: Domain Error.

    Attributes:
        hazard_code: The code that did not resolve.
    """

    def __init__(self, hazard_code: str) -> None:
        """Create the error.

        Args:
            hazard_code: The code that did not resolve.
        """
        super().__init__(
            f"hazard type {hazard_code!r} does not exist",
            details={"hazard_code": hazard_code},
        )
        self.hazard_code = hazard_code


class HazardCodeAlreadyUsedError(ConflictError):
    """The code already belongs to a hazard type, possibly a retired one.

    Retired codes are never reused (``AGENTS.md`` §7, Hazard type), so this is raised
    for retired codes too.

    Implements: Domain Error.

    Attributes:
        hazard_code: The code that is already taken.
    """

    def __init__(self, hazard_code: str) -> None:
        """Create the error.

        Args:
            hazard_code: The code that is already taken.
        """
        super().__init__(
            f"hazard code {hazard_code!r} is already used and can never be reused",
            details={"hazard_code": hazard_code},
        )
        self.hazard_code = hazard_code


class HazardTypeRetiredError(InvalidTransitionError):
    """The operation is not allowed because the hazard type is retired.

    Implements: Domain Error.

    Attributes:
        hazard_code: The retired hazard type.
        action: What was attempted, for example ``"retire"`` or ``"reparent"``.
    """

    def __init__(self, hazard_code: str, action: str) -> None:
        """Create the error.

        Args:
            hazard_code: The retired hazard type.
            action: What was attempted.
        """
        super().__init__(
            f"hazard type {hazard_code!r} is retired; cannot {action}",
            details={"hazard_code": hazard_code, "action": action},
        )
        self.hazard_code = hazard_code
        self.action = action


class HazardTypeNotRetiredError(InvalidTransitionError):
    """Reactivation was requested for a hazard type that is already active.

    Implements: Domain Error.

    Attributes:
        hazard_code: The active hazard type.
    """

    def __init__(self, hazard_code: str) -> None:
        """Create the error.

        Args:
            hazard_code: The active hazard type.
        """
        super().__init__(
            f"hazard type {hazard_code!r} is not retired; cannot reactivate",
            details={"hazard_code": hazard_code},
        )
        self.hazard_code = hazard_code


class InvalidTaxonomyError(InvariantViolationError):
    """The hazard taxonomy would contain a cycle, a duplicate or a dangling parent.

    Implements: Domain Error.
    """


class UnknownHazardAttributesError(ValidationError):
    """No attribute schema is registered under the given code.

    Implements: Domain Error.

    Attributes:
        schema_code: The unregistered attribute schema code.
    """

    def __init__(self, schema_code: str) -> None:
        """Create the error.

        Args:
            schema_code: The unregistered attribute schema code.
        """
        super().__init__(
            f"no hazard attribute schema is registered for {schema_code!r}",
            details={"schema_code": schema_code},
        )
        self.schema_code = schema_code


class HazardAttributesRegistrationError(InvariantViolationError):
    """An attribute schema was registered twice or under a code it does not declare.

    This is a programming mistake in the registry set-up, not bad input.

    Implements: Domain Error.
    """
