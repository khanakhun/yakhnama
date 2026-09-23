"""RFC 9457 Problem Details bodies returned for every mapped error.

The models only describe the body; the mapping from exceptions to statuses lives in
the exception handlers registered by ``yakhnama.main`` (``AGENTS.md`` §2.3). Phase 2
extends the body (problem ``type`` URIs, an ``instance``, a correlation id).

Patterns: API Schema (proposed in ADR 0011).
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

PROBLEM_JSON_MEDIA_TYPE: Final = "application/problem+json"
# RFC 9457 §4.2.1: "about:blank" means the status code's title is the whole semantics.
DEFAULT_PROBLEM_TYPE: Final = "about:blank"


class ProblemFieldError(BaseModel):
    """One invalid field: where it is and what is wrong, never the value itself.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        loc: Path to the offending field, for example ``["body", "limit"]``.
        msg: Human-readable reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    loc: tuple[str | int, ...]
    msg: str


class ProblemDetails(BaseModel):
    """An RFC 9457 problem object.

    Implements: API Schema (proposed in ADR 0011).

    Attributes:
        type: Problem type URI; ``"about:blank"`` until Phase 2 defines types.
        title: Short summary, the HTTP status phrase.
        status: The HTTP status code, repeated for clients that lose the header.
        detail: Explanation for this occurrence, safe to show; omitted when absent.
        errors: Field errors for validation problems; omitted otherwise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = DEFAULT_PROBLEM_TYPE
    title: str
    status: int = Field(ge=400, le=599)
    detail: str | None = None
    errors: tuple[ProblemFieldError, ...] | None = None
