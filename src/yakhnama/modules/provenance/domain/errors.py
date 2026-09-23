"""Errors of the ``provenance`` bounded context.

Every class subclasses a shared-kernel family, so the API maps it to Problem Details
by family without importing this module (``AGENTS.md`` §2.3). Messages are fixed
strings that never repeat an input value: a citation or URL can name a person or
carry an access token, so only ids and short rule descriptions travel in
``details``.

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import Self

from yakhnama.shared_kernel.errors import (
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from yakhnama.shared_kernel.ids import EntityId


class SourceNotFoundError(NotFoundError):
    """No source exists with the requested id.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, source_id: EntityId) -> Self:
        """Build the error for a missing source id.

        Args:
            source_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls("no such source", details={"source_id": str(source_id)})


class SourceImmutableError(InvalidTransitionError):
    """A referenced source was asked to change.

    A source is immutable once any fact references it (``AGENTS.md`` §7), because
    changing it would silently change the provenance of every fact that cites it.
    A correction is a new source.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, source_id: EntityId) -> Self:
        """Build the error for a change attempted on a referenced source.

        Args:
            source_id: Id of the referenced source.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(
            "the source is referenced and can no longer change; register a new "
            "source instead",
            details={"source_id": str(source_id)},
        )


class InvalidSourceUrlError(ValidationError):
    """A source URL breaks the ``SourceUrl`` rules, checked outside a model.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def because(cls, reason: str) -> Self:
        """Build the error with the rule that failed.

        Args:
            reason: The failed rule, one of the fixed validator messages; never the
                URL itself.

        Returns:
            The error, with the reason in ``details``.
        """
        return cls("invalid source url", details={"reason": reason})
