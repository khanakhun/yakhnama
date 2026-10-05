"""Ports the geography application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1).

The boundary ports (ADR 0021) keep download, parsing and geometry out of the
application layer: ``BoundaryLoader`` reads a pinned boundary file,
``SharedEdgeCalculator`` turns polygons into shared edges (Shapely is an
infrastructure library, ``AGENTS.md`` §2.1), ``DistrictEdgeSetRepository`` stores the
snapshots and ``DistrictEdgeQueryService`` serves the current one.

Patterns: Repository (port side), Unit of Work, Query Service, Adapter (port side).
"""

from typing import Protocol

from yakhnama.modules.geography.application.dto import (
    DistrictEdgeSnapshot,
    PlaceDetail,
    PlaceSummary,
    SharedEdgeComputation,
)
from yakhnama.modules.geography.application.queries import SearchPlaces
from yakhnama.modules.geography.domain.boundaries import (
    DistrictBoundarySet,
    DistrictBoundarySource,
    DistrictEdgeSet,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import AdminLevel
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

    async def list_at_level(self, level: AdminLevel) -> tuple[Place, ...]:
        """Return every active place at ``level``, ordered by code.

        Args:
            level: The administrative level.

        Returns:
            The aggregates, possibly none.
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


class DistrictEdgeSetRepository(Protocol):
    """Stores and loads immutable ``DistrictEdgeSet`` snapshots.

    Implements: Repository (port side).
    """

    async def add(self, edge_set: DistrictEdgeSet) -> None:
        """Stage a new snapshot; it becomes current once committed.

        Args:
            edge_set: The snapshot.

        Raises:
            ConflictError: If a snapshot with the same id exists.
        """
        ...

    async def get_current(self) -> DistrictEdgeSet | None:
        """Return the newest snapshot, staged ones included.

        Returns:
            The snapshot with the latest ``created_at`` (then the greatest id), or
            ``None`` if none is stored.
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

    @property
    def district_edge_sets(self) -> DistrictEdgeSetRepository:
        """Return the district edge snapshot repository of this transaction."""
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


class DistrictEdgeQueryService(Protocol):
    """Read port for the published shared district edges.

    Implements: Query Service.
    """

    async def get_current(self) -> DistrictEdgeSnapshot | None:
        """Return the newest committed snapshot as its public collection.

        Returns:
            The snapshot's id and collection, or ``None`` if none is stored.
        """
        ...


class BoundaryLoader(Protocol):
    """Obtains and reads the boundary file a ``DistrictBoundarySource`` pins.

    Implements: Adapter (port side).
    """

    async def load(self, source: DistrictBoundarySource) -> DistrictBoundarySet:
        """Return the region's districts from the verified file.

        Args:
            source: The committed description: URL, SHA-256, archive member and
                region.

        Returns:
            Every district of ``source.region_code`` with its polygon.

        Raises:
            BoundarySourceError: If the file cannot be obtained, its SHA-256 is not
                ``source.sha256``, or its content is not the expected GeoJSON.
        """
        ...


class SharedEdgeCalculator(Protocol):
    """Computes the edges the districts of one region share.

    Implements: Adapter (port side).
    """

    def compute(self, boundary_set: DistrictBoundarySet) -> SharedEdgeComputation:
        """Return the simplified shared edges of every pair of adjacent districts.

        No returned line lies on the outer edge of the region (the union of every
        district of ``boundary_set``).

        Args:
            boundary_set: The region's districts.

        Returns:
            The edges and what the calculation noticed about the polygons.
        """
        ...
