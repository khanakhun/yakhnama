"""Request and response bodies of the impacts HTTP API.

Detail responses are the application's ``ImpactMetricDetail`` DTO as it is: DTOs
are the public read models and already bounded. This module adds the listing query
string and the page envelope.

Patterns: API Schema.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.impacts.application.dto import ImpactMetricSummary
from yakhnama.modules.impacts.public import MetricCategory
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)

METRIC_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"
METRIC_CODE_MAX_LENGTH = 64


class ListImpactMetricsParameters(BaseModel):
    """Query string of ``GET /api/v1/impact-metrics``.

    Implements: API Schema.

    Attributes:
        category: Only list metrics of this category.
        include_retired: Also list retired metrics.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: MetricCategory | None = None
    include_retired: bool = False
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class ImpactMetricPage(BaseModel):
    """One page of impact metrics, ordered by code.

    Implements: API Schema.

    Attributes:
        items: The metrics on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ImpactMetricSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)
