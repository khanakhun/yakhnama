"""Ports the provenance application layer depends on, and the ports it offers.

``SourceRepository``, the unit of work and ``SourceQueryService`` are bound to
adapters by ``yakhnama.main`` and ``yakhnama.platform.container`` only
(``AGENTS.md`` §2.1). ``SourceRegistrar`` and ``SourceReferenceMarker`` go the
other way: they describe ``RegisterSourceHandler`` and
``MarkSourceReferencedHandler`` as callables, so other modules (reports, media,
events, impacts) can depend on the shape through ``provenance.public`` and receive
the real handlers from the composition root, and their tests can use fakes.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from typing import Protocol

from yakhnama.modules.provenance.application.commands import (
    MarkSourceReferenced,
    RegisterSource,
)
from yakhnama.modules.provenance.application.dto import SourceDetail, SourceSummary
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page, PageRequest
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class SourceRepository(Protocol):
    """Loads and stages ``Source`` aggregates inside one unit of work.

    There is no delete: a source is provenance and is never removed.

    Implements: Repository (port side).
    """

    async def get(self, source_id: EntityId) -> Source | None:
        """Return the source with ``source_id``.

        Args:
            source_id: The source's id.

        Returns:
            The aggregate, or ``None``.
        """
        ...

    async def add(self, source: Source) -> None:
        """Stage a newly registered source.

        Args:
            source: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        ...

    async def save(self, source: Source) -> None:
        """Stage a changed source, checking optimistic concurrency.

        Args:
            source: The new state; its ``version`` is one more than the stored one.

        Raises:
            NotFoundError: If no source with that id exists.
            ConflictError: If the stored version is not ``source.version - 1``.
        """
        ...


class ProvenanceUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the provenance repositories.

    Implements: Unit of Work.
    """

    @property
    def sources(self) -> SourceRepository:
        """Return the source repository bound to this transaction."""
        ...


type ProvenanceUnitOfWorkFactory = UnitOfWorkFactory[ProvenanceUnitOfWork]
"""Opens a fresh provenance unit of work per use case."""


class SourceQueryService(Protocol):
    """Read port for sources.

    Authorisation is applied by ``AuthorisedSourceQueryService`` before this port is
    called; implementations only read.

    Implements: Query Service.
    """

    async def get_source(self, source_id: EntityId) -> SourceDetail | None:
        """Return one source.

        Args:
            source_id: The source.

        Returns:
            The detail view, or ``None``.
        """
        ...

    async def list_sources(
        self, specification: Specification[Source], page: PageRequest
    ) -> Page[SourceSummary]:
        """Return one page of the sources ``specification`` accepts.

        Ordered by ``created_at`` descending (newest first), then by id descending;
        the cursor's ``sort_key`` is ``created_at`` in ISO 8601 and its ``last_id``
        the last source's id.

        Args:
            specification: The filter, compiled to SQL by the adapter.
            page: Page size and cursor.

        Returns:
            Up to ``page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...


class SourceCitationChecker(Protocol):
    """Tells whether a published, verified event cites a source.

    Answered by the events and verification side in the composition root. Until
    one is bound, ``AuthorisedSourceQueryService`` treats every source as uncited,
    which keeps citizen and organisation sources private (fail closed).

    Implements: Adapter (port side).
    """

    async def is_cited_by_published_event(self, source_id: EntityId) -> bool:
        """Tell whether a published and verified event cites ``source_id``.

        Args:
            source_id: The source.

        Returns:
            ``True`` if at least one such event cites it, directly or through a
            linked report or impact claim.
        """
        ...


class SourceRegistrar(Protocol):
    """Registers a source on behalf of another module's use case.

    Bound to ``RegisterSourceHandler`` in the composition root.

    Implements: Command Handler (port side).
    """

    async def __call__(self, command: RegisterSource) -> SourceDetail:
        """Register the source.

        Args:
            command: The registration.

        Returns:
            The new source.

        Raises:
            PermissionDeniedError: If the actor may not register it.
        """
        ...


class SourceReferenceMarker(Protocol):
    """Marks a source as referenced on behalf of another module's use case.

    Bound to ``MarkSourceReferencedHandler`` in the composition root.

    Implements: Command Handler (port side).
    """

    async def __call__(self, command: MarkSourceReferenced) -> SourceDetail:
        """Mark the source as referenced; a no-op if it already is.

        Args:
            command: The source and the acting actor.

        Returns:
            The source after the change.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            SourceNotFoundError: If the source does not exist.
        """
        ...
