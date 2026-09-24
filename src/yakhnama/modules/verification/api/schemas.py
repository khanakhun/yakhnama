"""Request bodies, query strings and envelopes of the verification HTTP API.

Responses are the application's DTOs as they are: ``VerificationCaseDetail`` (with
its whole history, actors and reasons, which is why only moderators reach it) and
``VerificationCaseSummary``.

Patterns: API Schema.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from yakhnama.modules.verification.domain.value_objects import TransitionReason
from yakhnama.modules.verification.public import (
    TargetKind,
    VerificationCaseSummary,
    VerificationState,
)
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_PAGE_LIMIT,
)


class TransitionRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/verification/{case_id}/transitions``.

    There is no ``is_human`` member: every move made through the API is made by a
    person, so the router always sends ``is_human=True``.

    Implements: API Schema.

    Attributes:
        to_state: The requested state; it must follow from the current one.
        reason: Why; required for every target state except ``submitted``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    to_state: VerificationState
    reason: TransitionReason | None = None


class AssignmentRequest(BaseModel):
    """Body of ``POST /api/v1/moderation/verification/{case_id}/assignment``.

    Implements: API Schema.

    Attributes:
        reviewer_id: The user who becomes responsible; they must be allowed to
            review.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviewer_id: EntityId


class ListCasesParameters(BaseModel):
    """Query string of ``GET /api/v1/moderation/verification-cases``.

    Implements: API Schema.

    Attributes:
        state: Only cases currently in this state.
        target_kind: Only cases about reports, events or claims.
        assigned_to: Only cases assigned to this reviewer.
        cursor: Opaque cursor from a previous page.
        limit: Page size, 1 to 200.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: VerificationState | None = None
    target_kind: TargetKind | None = None
    assigned_to: EntityId | None = None
    cursor: Annotated[str | None, Field(max_length=MAX_CURSOR_LENGTH)] = None
    limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class VerificationCasePage(BaseModel):
    """One page of verification cases, oldest first.

    Implements: API Schema.

    Attributes:
        items: The cases on this page.
        next_cursor: Cursor of the next page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[VerificationCaseSummary, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(max_length=MAX_CURSOR_LENGTH)
