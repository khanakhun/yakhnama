"""In-memory fakes of the impacts ports.

The repository stages writes until the unit of work commits, exactly as a rolled-back
database transaction would leave the table unchanged. The query service reads the
repository's committed rows through the same specifications the SQL implementation
compiles, and pages by keyset on ``code``.

Patterns: Fake.
"""

from collections.abc import Iterable

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.impacts.application.dto import (
    ImpactMetricDetail,
    ImpactMetricSummary,
)
from yakhnama.modules.impacts.application.queries import ListImpactMetrics
from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.registry import ImpactMetricRegistry
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


class InMemoryImpactMetricRepository:
    """``ImpactMetricRepository`` over a dictionary keyed by code.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored metrics, as a committed transaction left them.
    """

    def __init__(self, metrics: Iterable[ImpactMetric] = ()) -> None:
        """Create the repository.

        Args:
            metrics: Metrics that exist before the test acts.
        """
        self.committed: dict[str, ImpactMetric] = {
            metric.code: metric for metric in metrics
        }
        self._staged: dict[str, ImpactMetric] = {}

    def _current(self) -> dict[str, ImpactMetric]:
        return {**self.committed, **self._staged}

    async def get_by_code(self, code: str) -> ImpactMetric | None:
        """Return the metric with ``code``, staged changes included.

        Args:
            code: The metric code.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(code)

    async def list_all(self) -> ImpactMetricRegistry:
        """Return every stored and staged metric, ordered by code.

        Returns:
            The registry.
        """
        current = self._current()
        return ImpactMetricRegistry.from_metrics(
            current[code] for code in sorted(current)
        )

    async def add(self, metric: ImpactMetric) -> None:
        """Stage a new metric.

        Args:
            metric: The new aggregate.

        Raises:
            ConflictError: If the code or the id is already taken.
        """
        current = self._current()
        if metric.code in current or any(
            stored.id == metric.id for stored in current.values()
        ):
            message = f"impact metric {metric.code!r} already exists"
            raise ConflictError(message)
        self._staged[metric.code] = metric

    async def save(self, metric: ImpactMetric) -> None:
        """Stage a changed metric.

        Args:
            metric: The new state of a stored aggregate.

        Raises:
            NotFoundError: If no stored metric has this code and id.
        """
        stored = self._current().get(metric.code)
        if stored is None or stored.id != metric.id:
            message = f"impact metric {metric.code!r} is not stored"
            raise NotFoundError(message)
        self._staged[metric.code] = metric

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryImpactsUnitOfWork(InMemoryUnitOfWork):
    """``ImpactsUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        impact_metrics: The repository bound to this unit of work.
    """

    def __init__(self, metrics: Iterable[ImpactMetric] = ()) -> None:
        """Create the unit of work.

        Args:
            metrics: Metrics that exist before the test acts.
        """
        super().__init__()
        self.impact_metrics = InMemoryImpactMetricRepository(metrics)

    def _on_commit(self) -> None:
        self.impact_metrics.apply_staged()

    def _on_rollback(self) -> None:
        self.impact_metrics.discard_staged()


class InMemoryImpactMetricQueryService:
    """``ImpactMetricQueryService`` reading a fake repository's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, repository: InMemoryImpactMetricRepository) -> None:
        """Create the query service.

        Args:
            repository: The repository whose committed rows are served.
        """
        self._repository = repository

    async def list_impact_metrics(
        self, query: ListImpactMetrics
    ) -> Page[ImpactMetricSummary]:
        """Filter, order by code and page like the SQL implementation.

        Args:
            query: Filters and page request.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        specification = query.to_specification()
        cursor = query.page.decode_cursor()
        after = "" if cursor is None else cursor.sort_key
        matching = [
            (metric, summary)
            for metric in sorted(
                self._repository.committed.values(), key=lambda item: item.code
            )
            if metric.code > after
            for summary in (ImpactMetricSummary.from_entity(metric),)
            if specification is None or specification.is_satisfied_by(summary)
        ]
        window = matching[: query.page.limit]
        next_cursor = None
        if len(matching) > query.page.limit:
            last = window[-1][0]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        return Page[ImpactMetricSummary](
            items=tuple(summary for _, summary in window), next_cursor=next_cursor
        )

    async def get(self, code: str) -> ImpactMetricDetail | None:
        """Return the detail view of one committed metric.

        Args:
            code: The metric code.

        Returns:
            The detail view, or ``None``.
        """
        metric = self._repository.committed.get(code)
        return None if metric is None else ImpactMetricDetail.from_entity(metric)
