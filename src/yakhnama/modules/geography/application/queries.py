"""Read requests accepted by the geography query services.

Patterns: Query, Specification.
"""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from yakhnama.modules.geography.application.specifications import (
    ActivePlaceSpecification,
    PlaceLevelSpecification,
    PlaceTextSpecification,
    fold_search_text,
)
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.value_objects import AdminLevel
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import PageRequest
from yakhnama.shared_kernel.specification import Specification
from yakhnama.shared_kernel.value_objects import LanguageCode

SEARCH_TEXT_MAX_LENGTH = 100


def _require_text_after_folding(value: str) -> str:
    """Reject input that folds to nothing, which would otherwise match every name.

    Combining marks alone (for example ``"\u0301"``) pass the length check but
    fold to an empty string, and an empty pattern is a full-table wildcard.
    """
    if fold_search_text(value) == "":
        message = "search text is empty after folding"
        raise ValueError(message)
    return value


SearchText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=SEARCH_TEXT_MAX_LENGTH
    ),
    AfterValidator(_require_text_after_folding),
]
"""Place search input: 1 to 100 characters after stripping, non-empty once folded."""


class SearchPlaces(BaseModel):
    """Ask for one page of places whose names match a text.

    Implements: Query.

    Attributes:
        text: The search text, 1 to 100 characters after stripping.
        language: Match only names in this language, and show names in it, if set.
        level: Only return places at this level, if set.
        include_inactive: Also return merged and retired places.
        page: Page size and cursor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: SearchText
    language: LanguageCode | None = None
    level: AdminLevel | None = None
    include_inactive: bool = False
    page: PageRequest = PageRequest()

    def to_specification(self) -> Specification[Place]:
        """Combine the set filters into one specification.

        Returns:
            The text match, conjoined with the level and active-only filters when
            they apply.
        """
        combined: Specification[Place] = PlaceTextSpecification(
            self.text, self.language
        )
        if self.level is not None:
            combined = combined.and_(PlaceLevelSpecification(self.level))
        if not self.include_inactive:
            combined = combined.and_(ActivePlaceSpecification())
        return combined


class GetPlace(BaseModel):
    """Ask for one place by id, whatever its status.

    Implements: Query.

    Attributes:
        place_id: The place id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_id: EntityId
