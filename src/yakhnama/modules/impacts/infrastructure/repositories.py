"""SQLAlchemy adapter of the impacts repository port.

Writes go straight to the unit of work's transaction inside a savepoint, and row
objects are expunged as soon as they are read or written, so the database is the
only state (see the geography repository for the same reasoning).

Optimistic concurrency: the repository remembers the ``version`` of every metric it
loaded (by code or through ``list_all``) or stored in this unit of work, and ``save``
updates the row only ``WHERE version = <remembered version>``. A metric saved without
having been loaded here is assumed to carry exactly one change (expected version
``version - 1``), which fails safe.

Patterns: Repository (adapter side).
"""

from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import ImpactMetricNotFoundError
from yakhnama.modules.impacts.domain.registry import ImpactMetricRegistry
from yakhnama.modules.impacts.infrastructure.mappers import (
    metric_to_row,
    row_to_metric,
    to_json,
)
from yakhnama.modules.impacts.infrastructure.orm import ImpactMetricRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError

if TYPE_CHECKING:
    from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyImpactMetricRepository:
    """PostgreSQL-backed implementation of ``ImpactMetricRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``ImpactMetricRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get_by_code(self, code: str) -> ImpactMetric | None:
        """Return the metric with ``code``, active or retired.

        Args:
            code: The metric code.

        Returns:
            The aggregate, or ``None`` if no metric has that code.
        """
        row = (
            await self._session.execute(
                select(ImpactMetricRow).where(ImpactMetricRow.code == code)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._track(row)

    async def list_all(self) -> ImpactMetricRegistry:
        """Return every metric ever defined, retired ones included.

        Returns:
            The whole registry, ordered by code.
        """
        rows = (
            await self._session.execute(
                select(ImpactMetricRow).order_by(ImpactMetricRow.code)
            )
        ).scalars()
        return ImpactMetricRegistry.from_metrics([self._track(row) for row in rows])

    async def add(self, metric: ImpactMetric) -> None:
        """Insert a new metric.

        Args:
            metric: The new aggregate.

        Raises:
            ConflictError: If a metric with the same code or id exists.
        """
        row = metric_to_row(metric)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"impact metric {metric.code!r} already exists"
            raise ConflictError(message, details={"code": metric.code}) from error
        self._session.expunge(row)
        self._versions[metric.id] = metric.version

    async def save(self, metric: ImpactMetric) -> None:
        """Update a stored metric.

        Args:
            metric: The new state of an existing aggregate.

        Raises:
            ImpactMetricNotFoundError: If no metric has this id and code.
            ConflictError: If the stored version is not the one this unit of work
                loaded.
        """
        expected = self._versions.get(metric.id, metric.version - 1)
        statement = (
            update(ImpactMetricRow)
            .where(
                ImpactMetricRow.id == metric.id,
                ImpactMetricRow.code == metric.code,
                ImpactMetricRow.version == expected,
            )
            .values(
                labels=metric.labels.model_dump(mode="json"),
                description=to_json(metric.description),
                category=metric.category.value,
                value_kind=metric.value_kind.value,
                unit=metric.unit,
                currency=metric.currency,
                sendai=to_json(metric.sendai),
                desinventar=to_json(metric.desinventar),
                aggregation=metric.aggregation,
                status=metric.status.value,
                retirement=to_json(metric.retirement),
                version=metric.version,
                updated_at=metric.updated_at,
            )
            .returning(ImpactMetricRow.id)
            .execution_options(synchronize_session=False)
        )
        updated = (await self._session.execute(statement)).scalar_one_or_none()
        if updated is None:
            await self._raise_missing_or_stale(metric, expected)
        self._versions[metric.id] = metric.version

    def _track(self, row: ImpactMetricRow) -> ImpactMetric:
        self._session.expunge(row)
        metric = row_to_metric(row)
        self._versions[metric.id] = metric.version
        return metric

    async def _raise_missing_or_stale(
        self, metric: ImpactMetric, expected: int
    ) -> None:
        stored = await self._session.scalar(
            select(ImpactMetricRow.version).where(
                ImpactMetricRow.id == metric.id,
                ImpactMetricRow.code == metric.code,
            )
        )
        if stored is None:
            message = f"impact metric {metric.code!r} is not stored"
            raise ImpactMetricNotFoundError(message, details={"code": metric.code})
        message = (
            f"impact metric {metric.code!r} was changed concurrently (expected "
            f"version {expected}, stored {stored})"
        )
        raise ConflictError(
            message,
            details={"code": metric.code, "expected": expected, "stored": stored},
        )
