"""Read requests accepted by the impacts query services.

Patterns: Query, Specification.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.impacts.application.dto import ImpactMetricSummary
from yakhnama.modules.impacts.application.specifications import (
    ActiveImpactMetricSpecification,
    ImpactMetricCategorySpecification,
)
from yakhnama.modules.impacts.domain.value_objects import MetricCategory, MetricCode
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import Specification


class ListImpactMetrics(BaseModel):
    """Ask for one page of impact metrics, ordered by code.

    Implements: Query.

    Attributes:
        category: Only return metrics of this category, if set.
        include_retired: Also return retired metrics; ``False`` by default because
            new claims may only use active ones.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: MetricCategory | None = None
    include_retired: bool = False
    page: PageRequest = PageRequest()

    def to_specification(self) -> Specification[ImpactMetricSummary] | None:
        """Combine the set filters into one specification.

        Returns:
            The conjunction of the set filters, or ``None`` when every metric
            matches.
        """
        filters: list[Specification[ImpactMetricSummary]] = []
        if self.category is not None:
            filters.append(ImpactMetricCategorySpecification(self.category))
        if not self.include_retired:
            filters.append(ActiveImpactMetricSpecification())
        if not filters:
            return None
        combined = filters[0]
        for specification in filters[1:]:
            combined = combined.and_(specification)
        return combined


class GetImpactMetric(BaseModel):
    """Ask for one impact metric by code, active or retired.

    Implements: Query.

    Attributes:
        code: The metric code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MetricCode
