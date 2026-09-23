"""Ports the hazards application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1).

Patterns: Repository (port side), Unit of Work, Query Service.
"""

from typing import Protocol

from yakhnama.modules.hazards.application.dto import (
    HazardTypeDetail,
    HazardTypeSummary,
)
from yakhnama.modules.hazards.application.queries import ListHazardTypes
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class HazardTypeRepository(Protocol):
    """Loads and stages ``HazardType`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get_by_code(self, code: str) -> HazardType | None:
        """Return the hazard type with ``code``, active or retired.

        Args:
            code: The hazard code.

        Returns:
            The aggregate, or ``None`` if no type has that code.
        """
        ...

    async def list_all(self) -> HazardTaxonomy:
        """Return every hazard type ever defined, retired ones included.

        Returns:
            The whole taxonomy, validated as one tree.
        """
        ...

    async def add(self, hazard_type: HazardType) -> None:
        """Stage a new hazard type; its parent must already be stored or staged.

        Args:
            hazard_type: The new aggregate at version 1.

        Raises:
            ConflictError: If a hazard type with the same code or id exists.
        """
        ...

    async def save(self, hazard_type: HazardType) -> None:
        """Stage a changed hazard type.

        Args:
            hazard_type: The new state of an existing aggregate.

        Raises:
            NotFoundError: If no hazard type with that id exists.
        """
        ...


class HazardsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the hazards repositories.

    Implements: Unit of Work.
    """

    @property
    def hazard_types(self) -> HazardTypeRepository:
        """Return the hazard type repository bound to this transaction."""
        ...


type HazardsUnitOfWorkFactory = UnitOfWorkFactory[HazardsUnitOfWork]
"""Opens a fresh hazards unit of work per use case."""


class HazardTypeQueryService(Protocol):
    """Read port for hazard types.

    Implements: Query Service.
    """

    async def list_hazard_types(
        self, query: ListHazardTypes
    ) -> Page[HazardTypeSummary]:
        """Return one page of hazard types matching ``query``, ordered by code.

        Args:
            query: Filters and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get(self, code: str) -> HazardTypeDetail | None:
        """Return one hazard type, active or retired.

        Args:
            code: The hazard code.

        Returns:
            The detail view, or ``None`` if no type has that code.
        """
        ...
