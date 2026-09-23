"""Errors of the ``geography`` bounded context.

Every class subclasses one of the shared-kernel families, so the API maps it to Problem
Details by family without importing this module (``AGENTS.md`` §2.3). Constructors take
the identifying values and build a message that never contains personal data; place
codes and names are public reference data.

Patterns: Domain Error (proposed in ADR 0012).
"""

from typing import Self

from yakhnama.shared_kernel.errors import (
    ConflictError,
    InvalidTransitionError,
    InvariantViolationError,
    NotFoundError,
)
from yakhnama.shared_kernel.ids import EntityId


class PlaceNotFoundError(NotFoundError):
    """No place exists with the requested id or code.

    Implements: Domain Error (proposed in ADR 0012).
    """

    @classmethod
    def for_id(cls, place_id: EntityId) -> Self:
        """Build the error for a missing place id.

        Args:
            place_id: The id that was looked up.

        Returns:
            The error, with the id in ``details``.
        """
        return cls(f"no place with id {place_id}", details={"place_id": str(place_id)})

    @classmethod
    def for_code(cls, code: str) -> Self:
        """Build the error for a missing place code.

        Args:
            code: The code that was looked up.

        Returns:
            The error, with the code in ``details``.
        """
        return cls(f"no place with code {code!r}", details={"code": code})


class PlaceNameNotFoundError(NotFoundError):
    """A place has no name matching the requested language, text and script.

    Implements: Domain Error (proposed in ADR 0012).
    """


class DuplicatePlaceNameError(ConflictError):
    """A place already has a name with the same text, language and script.

    Implements: Domain Error (proposed in ADR 0012).
    """


class InvalidPlaceHierarchyError(InvariantViolationError):
    """A place's parent is missing, forbidden or not at a strictly higher level.

    The rule it enforces is a proposed default, see ``AdminLevel.can_be_child_of``.

    Implements: Domain Error (proposed in ADR 0012).
    """


class PlaceRetiredError(InvalidTransitionError):
    """The place is retired or merged and can no longer be changed.

    Implements: Domain Error (proposed in ADR 0012).
    """
