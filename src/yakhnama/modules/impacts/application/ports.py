"""Ports the impacts application layer depends on.

Only ``yakhnama.main`` and ``yakhnama.platform.container`` bind these protocols to
adapters (``AGENTS.md`` §2.1).

Patterns: Repository (port side), Unit of Work, Query Service.
"""

from typing import Protocol

from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
)
from yakhnama.modules.impacts.application.queries import ListImpactMetrics
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.registry import ImpactMetricRegistry
from yakhnama.shared_kernel.pagination import Page
from yakhnama.shared_kernel.uow import UnitOfWork, UnitOfWorkFactory


class ImpactMetricRepository(Protocol):
    """Loads and stages ``ImpactMetric`` aggregates inside one unit of work.

    Implements: Repository (port side).
    """

    async def get_by_code(self, code: str) -> ImpactMetric | None:
        """Return the metric with ``code``, active or retired.

        Args:
            code: The metric code.

        Returns:
            The aggregate, or ``None`` if no metric has that code.
        """
        ...

    async def list_all(self) -> ImpactMetricRegistry:
        """Return every metric ever defined, retired ones included.

        Returns:
            The whole registry, ordered by code.
        """
        ...

    async def add(self, metric: ImpactMetric) -> None:
        """Stage a new metric.

        Args:
            metric: The new aggregate.

        Raises:
            ConflictError: If a metric with the same code or id exists.
        """
        ...

    async def save(self, metric: ImpactMetric) -> None:
        """Stage a changed metric.

        Args:
            metric: The new state of an existing aggregate.

        Raises:
            NotFoundError: If no metric with that id exists.
        """
        ...


class ImpactsUnitOfWork(UnitOfWork, Protocol):
    """Transaction boundary exposing the impacts repositories.

    Implements: Unit of Work.
    """

    @property
    def impact_metrics(self) -> ImpactMetricRepository:
        """Return the impact metric repository bound to this transaction."""
        ...


type ImpactsUnitOfWorkFactory = UnitOfWorkFactory[ImpactsUnitOfWork]
"""Opens a fresh impacts unit of work per use case."""


class ImpactMetricQueryService(Protocol):
    """Read port for impact metrics.

    Implements: Query Service.
    """

    async def list_impact_metrics(
        self, query: ListImpactMetrics
    ) -> Page[ImpactMetricSummary]:
        """Return one page of metrics matching ``query``, ordered by code.

        Args:
            query: Filters and page request.

        Returns:
            Up to ``query.page.limit`` summaries and the next cursor, if any.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        ...

    async def get(self, code: str) -> ImpactMetricDetail | None:
        """Return one metric, active or retired.

        Args:
            code: The metric code.

        Returns:
            The detail view, or ``None`` if no metric has that code.
        """
        ...
