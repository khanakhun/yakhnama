"""Request and response bodies of the hazards HTTP API.

Detail responses are the application's ``HazardTypeDetail`` DTO as it is: DTOs are
the public read models and already bounded. This module adds the listing query
string and the page envelope.

Patterns: API Schema.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.hazards.application.dto import HazardTypeSummary
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)

HAZARD_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"
HAZARD_CODE_MAX_LENGTH = 64


class ListHazardTypesParameters(BaseModel):
    """Query string of ``GET /api/v1/hazard-types``.

    Implements: API Schema.

    Attributes:
        include_retired: Also list retired types.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    include_retired: bool = False
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class HazardTypePage(BaseModel):
    """One page of hazard types, ordered by code.

    Implements: API Schema.

    Attributes:
        items: The hazard types on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[HazardTypeSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)
