"""Filter specifications for hazard type listings.

Leaves are evaluated in memory by fakes and compiled to SQL by the infrastructure
query service, so both filter by exactly the same rule (``AGENTS.md`` §3).

Patterns: Specification.
"""

from yakhnama.modules.hazards.application.dto import HazardTypeSummary
from yakhnama.modules.hazards.domain.value_objects import HazardTypeStatus
from yakhnama.shared_kernel.specification import Specification


class ActiveHazardTypeSpecification(Specification[HazardTypeSummary]):
    """Matches hazard types that still accept new classifications.

    Implements: Specification.
    """

    def is_satisfied_by(self, candidate: HazardTypeSummary) -> bool:
        """Tell whether ``candidate`` is active.

        Args:
            candidate: The hazard type to test.

        Returns:
            ``True`` if its status is ``active``.
        """
        return candidate.status is HazardTypeStatus.ACTIVE
