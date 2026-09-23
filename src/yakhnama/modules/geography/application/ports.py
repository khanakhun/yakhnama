"""Ports the geography application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1).

Patterns: Repository (port side), Unit of Work, Query Service.
"""

from typing import Protocol

from yakhnama.modules.geography.application.dto import PlaceDetail, PlaceSummary
from yakhnama.modules.geography.application.queries import SearchPlaces
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class PlaceRepository(Protocol):
    """Loads and stages ``Place`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get(self, place_id: EntityId) -> Place | None:
        """Return the place with ``place_id``, whatever its status.

        Args:
            place_id: The place id.

        Returns:
            The aggregate, or ``None`` if it does not exist.
        """
        ...

    async def get_by_code(self, code: str) -> Place | None:
        """Return the place with ``code``, whatever its status.

        Args:
            code: The place code.

        Returns:
            The aggregate, or ``None`` if no place has that code.
        """
        ...

    async def add(self, place: Place) -> None:
        """Stage a new place; its parent must already be stored or staged.

        Args:
            place: The new aggregate at version 1.

        Raises:
            ConflictError: If a place with the same id or code exists.
        """
        ...

    async def save(self, place: Place) -> None:
        """Stage a changed place.

        Args:
            place: The new state of an existing aggregate.

        Raises:
            NotFoundError: If no place with that id exists.
        """
        ...


class GeographyUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the geography repositories.

    Implements: Unit of Work.
    """

    @property
    def places(self) -> PlaceRepository:
        """Return the place repository bound to this transaction."""
        ...


type GeographyUnitOfWorkFactory = UnitOfWorkFactory[GeographyUnitOfWork]
"""Opens a fresh geography unit of work per use case."""


class PlaceQueryService(Protocol):
    """Read port for places.

    Implements: Query Service.
    """

    async def search(self, query: SearchPlaces) -> Page[PlaceSummary]:
        """Return one page of places matching ``query``.

        The SQL implementation orders by relevance; the order is stable for one
        query so the cursor stays valid.

        Args:
            query: Search text, filters and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get(self, place_id: EntityId) -> PlaceDetail | None:
        """Return one place, whatever its status.

        Args:
            place_id: The place id.

        Returns:
            The detail view, or ``None`` if it does not exist.
        """
        ...
