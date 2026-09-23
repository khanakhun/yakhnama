"""Filter specifications for impact metric listings.

Leaves are evaluated in memory by fakes and compiled to SQL by the infrastructure
query service, so both filter by exactly the same rule (``AGENTS.md`` §3).

Patterns: Specification.
"""

from yakhnama.modules.impacts.application.dto import ImpactMetricSummary
from yakhnama.modules.impacts.domain.value_objects import MetricCategory, MetricStatus
from yakhnama.shared_kernel.specification import Specification


class ActiveImpactMetricSpecification(Specification[ImpactMetricSummary]):
    """Matches metrics new claims may still use.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: ImpactMetricSummary) -> bool:
        """Tell whether ``candidate`` is active.

        Args:
            candidate: The metric to test.

        Returns:
            ``True`` if its status is ``active``.
        """
        return candidate.status is MetricStatus.ACTIVE


class ImpactMetricCategorySpecification(Specification[ImpactMetricSummary]):
    """Matches metrics of one category.

    Implements: Specification.

    Attributes:
        category: The category to match.
    """

    def __init__(self, category: MetricCategory) -> None:
        """Create the specification.

        Args:
            category: The category to match.
        """
        self._category = category

    @property
    def category(self) -> MetricCategory:
        """Return the category to match."""
        return self._category

    def is_satisfied_by(self, candidate: ImpactMetricSummary) -> bool:
        """Tell whether ``candidate`` belongs to the category.

        Args:
            candidate: The metric to test.

        Returns:
            ``True`` if its category is ``category``.
        """
        return candidate.category is self._category
