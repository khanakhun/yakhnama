"""Request bodies, query strings and envelopes of the provenance HTTP API.

Responses are the application's DTOs as they are: ``SourceDetail`` and
``SourceSummary`` never name the user who registered a source. The request body
reuses the domain's ``SourceDetails`` value object, so the API and the command
agree on every bound (safe text, URL scheme, licence, retrieval time).

Patterns: API Schema.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.provenance.public import SourceDetails, SourceSummary, SourceType
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)


class RegisterSourceRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/sources``.

    Implements: API Schema.

    Attributes:
        source_type: The kind of source; fixed from now on.
        details: Title, citation and the optional descriptive fields.
        organization_id: The organisation the source is registered for, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: SourceType
    details: SourceDetails
    organization_id: EntityId | None = None


class ListSourcesParameters(BaseModel):
    """Query string of ``GET /api/v1/sources``.

    Implements: API Schema.

    Attributes:
        source_type: Only sources of this type.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: SourceType | None = None
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class SourcePage(BaseModel):
    """One page of sources, newest first.

    Implements: API Schema.

    Attributes:
        items: The sources on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[SourceSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)
