"""Authorised read use cases of the provenance module.

``AuthorisedSourceQueryService`` checks the read policy for the query's actor and
then delegates to the ``SourceQueryService`` port, so the API never calls the port
unguarded.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.provenance.application.authorisation import (
    require_allowed,
    source_read_policy,
)
from yakhnama.modules.provenance.application.dto import SourceDetail, SourceSummary
from yakhnama.modules.provenance.application.ports import SourceQueryService
from yakhnama.modules.provenance.application.queries import GetSource, ListSources
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.shared_kernel.pagination import Page


class AuthorisedSourceQueryService:
    """Answer source queries for actors the read policy allows.

    Implements: Query Service.
    """

    def __init__(self, query_service: SourceQueryService) -> None:
        """Create the service.

        Args:
            query_service: The read port.
        """
        self._query_service = query_service

    async def get_source(self, query: GetSource) -> SourceDetail:
        """Return one source.

        Args:
            query: The actor and the source id.

        Returns:
            The source's detail view.

        Raises:
            PermissionDeniedError: If the read policy refuses the actor.
            SourceNotFoundError: If the source does not exist.
        """
        require_allowed(source_read_policy(), query.actor, action="read sources")
        source = await self._query_service.get_source(query.source_id)
        if source is None:
            raise SourceNotFoundError.for_id(query.source_id)
        return source

    async def list_sources(self, query: ListSources) -> Page[SourceSummary]:
        """Return one page of sources, newest first.

        Args:
            query: The actor, the optional type filter and the page request.

        Returns:
            The page.

        Raises:
            PermissionDeniedError: If the read policy refuses the actor.
            ValidationError: If the cursor is invalid.
        """
        require_allowed(source_read_policy(), query.actor, action="list sources")
        return await self._query_service.list_sources(
            query.to_specification(), query.page
        )
