"""Read requests accepted by the hazards query services.

Patterns: Query, Specification.
"""

from pydantic import BaseModel, ConfigDict

from yakhnama.modules.hazards.application.dto import HazardTypeSummary
from yakhnama.modules.hazards.application.specifications import (
    ActiveHazardTypeSpecification,
)
from yakhnama.modules.hazards.domain.value_objects import HazardCode
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import Specification


class ListHazardTypes(BaseModel):
    """Ask for one page of hazard types, ordered by code.

    Implements: Query.

    Attributes:
        include_retired: Also return retired types; ``False`` by default because
            new classifications may only use active ones.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    include_retired: bool = False
    page: PageRequest = PageRequest()

    def to_specification(self) -> Specification[HazardTypeSummary] | None:
        """Combine the set filters into one specification.

        Returns:
            The filter to apply, or ``None`` when every hazard type matches.
        """
        return None if self.include_retired else ActiveHazardTypeSpecification()


class GetHazardType(BaseModel):
    """Ask for one hazard type by code, active or retired.

    Implements: Query.

    Attributes:
        code: The hazard code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: HazardCode
