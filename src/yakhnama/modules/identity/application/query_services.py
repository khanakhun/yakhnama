"""Authorised read use cases of the identity module.

``ModeratorDirectoryQueryService`` answers ``ListModerators``: it refuses anyone
``moderator_directory_policy`` does not allow (deny by default) and returns at most
``MODERATOR_DIRECTORY_MAX`` active moderators and administrators, each as an id and
an optional display name. Suspended users are left out: a case assigned to them
could not be worked on.

Patterns: Query Service, Policy.
"""

from typing import Final

from yakhnama.modules.identity.application.authorisation import (
    moderator_directory_policy,
    require_allowed,
)
from yakhnama.modules.identity.application.dto import ModeratorSummary
from yakhnama.modules.identity.application.ports import IdentityQueryService
from yakhnama.modules.identity.application.queries import ListModerators

MODERATOR_DIRECTORY_MAX: Final = 500
"""Most entries the directory returns; far above any moderation team the platform
expects (**proposed**). A longer list would need paging and a search."""


class ModeratorDirectoryQueryService:
    """List the people a verification case can be assigned to.

    Implements: Query Service.
    """

    def __init__(self, query_service: IdentityQueryService) -> None:
        """Create the service.

        Args:
            query_service: The identity read port.
        """
        self._query_service = query_service

    async def list_moderators(
        self, query: ListModerators
    ) -> tuple[ModeratorSummary, ...]:
        """Return the active moderators and administrators.

        Args:
            query: The actor.

        Returns:
            Up to ``MODERATOR_DIRECTORY_MAX`` entries, by display name then id.

        Raises:
            PermissionDeniedError: If the actor may not moderate.
        """
        require_allowed(
            moderator_directory_policy(),
            query.actor,
            action="read the moderator directory",
        )
        return await self._query_service.list_moderators(MODERATOR_DIRECTORY_MAX)
