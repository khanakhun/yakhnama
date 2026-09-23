"""The in-memory registry of impact metrics.

Patterns: Registry.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yakhnama.modules.impacts.domain.entities import ImpactMetric
from yakhnama.modules.impacts.domain.errors import (
    ImpactMetricNotFoundError,
    MetricCodeAlreadyUsedError,
)
from yakhnama.modules.impacts.domain.value_objects import (
    ImpactMetricRef,
    MetricCategory,
)


def _duplicate_codes(metrics: Iterable[ImpactMetric]) -> list[str]:
    counts = Counter(metric.code for metric in metrics)
    return sorted(code for code, count in counts.items() if count > 1)


class ImpactMetricRegistry(BaseModel):
    """An immutable set of metrics with unique codes, retired ones included.

    Retired metrics stay in the registry so their codes can never be reused.
    Lookups return metrics in the order they were given.

    Implements: Registry.

    Attributes:
        metrics: Every metric, active and retired.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metrics: tuple[ImpactMetric, ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def _check_unique_codes(self) -> Self:
        duplicates = _duplicate_codes(self.metrics)
        if duplicates:
            message = f"duplicate metric codes: {duplicates}"
            raise ValueError(message)
        return self

    @classmethod
    def from_metrics(cls, metrics: Iterable[ImpactMetric]) -> Self:
        """Build a registry, rejecting a code used twice.

        Args:
            metrics: The metrics, in lookup order.

        Returns:
            The registry.

        Raises:
            MetricCodeAlreadyUsedError: If two metrics share a code.
        """
        collected = tuple(metrics)
        duplicates = _duplicate_codes(collected)
        if duplicates:
            message = f"metric codes used more than once: {duplicates}"
            raise MetricCodeAlreadyUsedError(message, details={"codes": duplicates})
        return cls(metrics=collected)

    def get(self, ref: ImpactMetricRef) -> ImpactMetric:
        """Return the metric with the referenced code.

        Args:
            ref: The metric reference.

        Returns:
            The metric, active or retired.

        Raises:
            ImpactMetricNotFoundError: If no metric has that code.
        """
        for metric in self.metrics:
            if metric.code == ref.code:
                return metric
        message = f"no impact metric with code {ref.code!r}"
        raise ImpactMetricNotFoundError(message, details={"code": ref.code})

    def has_code(self, code: str) -> bool:
        """Tell whether a code is taken, by an active or a retired metric.

        Args:
            code: A candidate metric code.

        Returns:
            ``True`` if a new metric must not use it.
        """
        return any(metric.code == code for metric in self.metrics)

    def codes(self) -> tuple[str, ...]:
        """Return every code, active and retired.

        Returns:
            The codes in registry order.
        """
        return tuple(metric.code for metric in self.metrics)

    def active(self) -> tuple[ImpactMetric, ...]:
        """Return the metrics new claims may use.

        Returns:
            The active metrics in registry order.
        """
        return tuple(metric for metric in self.metrics if metric.is_active)

    def by_category(self, category: MetricCategory) -> tuple[ImpactMetric, ...]:
        """Return the metrics of one category, active and retired.

        Args:
            category: The category.

        Returns:
            Matching metrics in registry order.
        """
        return tuple(metric for metric in self.metrics if metric.category is category)

    def by_sendai(self, code: str) -> tuple[ImpactMetric, ...]:
        """Return the metrics mapped to one Sendai indicator, active and retired.

        Args:
            code: A Sendai indicator code such as ``"A-1"``.

        Returns:
            Matching metrics in registry order.
        """
        return tuple(
            metric
            for metric in self.metrics
            if metric.sendai is not None and metric.sendai.code == code
        )
