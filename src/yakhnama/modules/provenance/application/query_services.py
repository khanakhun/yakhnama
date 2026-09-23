"""Authorised read use cases of the provenance module.

``AuthorisedSourceQueryService`` delegates to the ``SourceQueryService`` port and
applies the read rules of ``authorisation`` to what comes back, so the API never
calls the port unguarded. A source the actor may not read is reported as missing,
so its existence is not revealed.

Patterns: Query Service, Policy.
"""

from yakhnama.modules.provenance.application.authorisation import (
    source_listing_specification,
    source_read_policy,
)
from yakhnama.modules.provenance.application.dto import SourceDetail, SourceSummary
from yakhnama.modules.provenance.application.ports import (
    SourceCitationChecker,
    SourceQueryService,
)
from yakhnama.modules.provenance.application.queries import GetSource, ListSources
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.shared_kernel.pagination import Page


class AuthorisedSourceQueryService:
    """Answer source queries for actors the read policy allows.

    Implements: Query Service.
    """

    def __init__(
        self,
        query_service: SourceQueryService,
        citation_checker: SourceCitationChecker | None = None,
    ) -> None:
        """Create the service.

        Args:
            query_service: The read port.
            citation_checker: Tells whether a public event cites a source; when
                ``None`` no source counts as cited, so citizen and organisation
                sources stay private.
        """
        self._query_service = query_service
        self._citation_checker = citation_checker

    async def get_source(self, query: GetSource) -> SourceDetail:
        """Return one source.

        Args:
            query: The actor and the source id.

        Returns:
            The source's detail view.

        Raises:
            SourceNotFoundError: If the source does not exist, or the actor may not
                read it.
        """
        source = await self._query_service.get_source(query.source_id)
        if source is None:
            raise SourceNotFoundError.for_id(query.source_id)
        if source_read_policy(source).is_allowed(query.actor):
            return source
        if self._citation_checker is not None and (
            await self._citation_checker.is_cited_by_published_event(source.id)
        ):
            return source
        raise SourceNotFoundError.for_id(query.source_id)

    async def list_sources(self, query: ListSources) -> Page[SourceSummary]:
        """Return one page of sources, newest first.

        Args:
            query: The actor, the optional type filter and the page request.

        Returns:
            The page; without citizen and organisation sources for
            non-moderators.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        specification = query.to_specification().and_(
            source_listing_specification(query.actor)
        )
        return await self._query_service.list_sources(specification, query.page)
