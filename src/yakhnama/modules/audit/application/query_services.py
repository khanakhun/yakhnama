"""Authorised read use cases of the audit module.

Only platform administrators read the audit log (``IsAdmin``): it links users to
everything they changed, so it is personal data even though it holds ids only.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.audit.application.dto import AuditEntrySummary
from yakhnama.modules.audit.application.ports import AuditQueryService
from yakhnama.modules.audit.application.queries import (
    ListAuditEntriesForTarget,
    ListRecentAuditEntries,
)
from yakhnama.modules.identity.public import IsAdmin, require_allowed
from yakhnama.shared_kernel.pagination import Page


class AuthorisedAuditQueryService:
    """Answer audit queries for platform administrators only.

    Implements: Query Service.
    """

    def __init__(self, query_service: AuditQueryService) -> None:
        """Create the service.

        Args:
            query_service: The read port.
        """
        self._query_service = query_service

    async def list_for_target(
        self, query: ListAuditEntriesForTarget
    ) -> Page[AuditEntrySummary]:
        """Return one page of the entries about one aggregate, newest first.

        Args:
            query: The actor, target and page request.

        Returns:
            The page.

        Raises:
            PermissionDeniedError: If the actor is not a platform administrator.
            ValidationError: If the cursor is invalid.
        """
        require_allowed(IsAdmin(), query.actor, action="read the audit log")
        return await self._query_service.list_for_target(query.target, query.page)

    async def list_recent(
        self, query: ListRecentAuditEntries
    ) -> Page[AuditEntrySummary]:
        """Return one page of the whole log, newest first.

        Args:
            query: The actor and page request.

        Returns:
            The page.

        Raises:
            PermissionDeniedError: If the actor is not a platform administrator.
            ValidationError: If the cursor is invalid.
        """
        require_allowed(IsAdmin(), query.actor, action="read the audit log")
        return await self._query_service.list_recent(query.page)
