"""The error hierarchy every bounded context raises.

Every domain and application failure derives from ``YakhnamaError`` so the API can map
it to RFC 9457 Problem Details in exactly one place, the exception handlers registered
in ``yakhnama.main`` (``AGENTS.md`` §2.3). Each class carries a stable ``code`` slug
that the handlers can use for the problem ``type`` without importing module-specific
errors; modules subclass these errors instead of inventing new roots.

Validation inside Pydantic validators is the exception to the rule: a validator must
raise ``ValueError`` so Pydantic can collect every problem into one
``pydantic.ValidationError``. ``ValidationError`` below is raised by plain code paths
(parsers, factories, lookups) that are not running inside a Pydantic validator.

Patterns: Domain Error (proposed in ADR 0012).
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar


class YakhnamaError(Exception):
    """Root of every error raised deliberately by Yakhnama code.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: Stable, machine-readable slug for the error family.
        message: Human-readable explanation, safe to show to API clients; it must
            never contain personal data (``AGENTS.md`` §5).
        details: Read-only structured context (for example the offending field).
    """

    code: ClassVar[str] = "yakhnama_error"

    def __init__(
        self, message: str, *, details: Mapping[str, object] | None = None
    ) -> None:
        """Create the error.

        Args:
            message: Human-readable explanation of the failure.
            details: Optional structured context. It is copied into a read-only
                mapping so a caller mutating its own dictionary later cannot change
                an error that is already being handled.
        """
        super().__init__(message)
        self.message = message
        self.details: Mapping[str, object] = MappingProxyType(dict(details or {}))

    def __str__(self) -> str:
        """Return the message alone, without the details.

        Returns:
            The human-readable message given at construction.
        """
        return self.message


class NotFoundError(YakhnamaError):
    """A requested aggregate or resource does not exist.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"not_found"``.
    """

    code: ClassVar[str] = "not_found"


class ConflictError(YakhnamaError):
    """The request conflicts with the current state, for example a duplicate id.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"conflict"``.
    """

    code: ClassVar[str] = "conflict"


class ValidationError(YakhnamaError):
    """Input is malformed or violates a rule checked outside a Pydantic validator.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"validation_error"``.
    """

    code: ClassVar[str] = "validation_error"


class PermissionDeniedError(YakhnamaError):
    """The actor is not allowed to perform the action.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"permission_denied"``.
    """

    code: ClassVar[str] = "permission_denied"


class InvariantViolationError(YakhnamaError):
    """An aggregate or kernel invariant would be broken by the operation.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"invariant_violation"``.
    """

    code: ClassVar[str] = "invariant_violation"


class InvalidTransitionError(YakhnamaError):
    """A state machine was asked to make a transition its table does not allow.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"invalid_transition"``.
    """

    code: ClassVar[str] = "invalid_transition"


class PreconditionFailedError(YakhnamaError):
    """A conditional request's precondition, such as an ``If-Match`` version, failed.

    Proposed in ADR 0012 so that ``yakhnama.main`` can map it to HTTP 412 without
    importing module-specific errors.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"precondition_failed"``.
    """

    code: ClassVar[str] = "precondition_failed"


class PreconditionRequiredError(YakhnamaError):
    """A mutating request that must be conditional arrived without ``If-Match``.

    Without the header a client could overwrite a change it never saw (the lost
    update problem), so the API refuses the request instead of guessing (RFC 6585
    §3, HTTP 428).

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"precondition_required"``.
    """

    code: ClassVar[str] = "precondition_required"


class AuthenticationError(YakhnamaError):
    """The request carries no valid bearer token where one is required.

    Raised for a missing, malformed, expired or otherwise rejected token. The message
    is deliberately generic and never contains the token or the reason it failed, so
    a client (or an attacker) learns nothing about the validation (ADR 0015). The API
    maps it to HTTP 401 with ``WWW-Authenticate: Bearer``.

    Implements: Domain Error (proposed in ADR 0012).

    Attributes:
        code: ``"authentication_failed"``.
    """

    code: ClassVar[str] = "authentication_failed"
