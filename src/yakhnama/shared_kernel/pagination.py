"""Keyset (cursor) pagination primitives.

A cursor is an opaque, URL-safe token that tells the query service where the previous
page ended: the sort key and id of the last item and the direction of travel. Keyset
pagination stays stable while new reports arrive and costs the same on page 1 and page
1,000, unlike ``OFFSET``.

The token is base64url-encoded JSON and is **not signed or encrypted**: clients can
read it and craft their own. It therefore carries only values the client could already
see on the page it came from, never secrets or personal data, and query services must
treat a decoded cursor as untrusted input (it only positions a query that authorisation
has already scoped). A token is accepted only if it is exactly what ``encode_cursor``
produces for the payload it decodes to, so a corrupted, truncated or re-encoded token
fails loudly instead of silently starting somewhere else. A deliberately crafted,
well-formed token is accepted by design; detecting that would need a signature, which
is an open question.

Patterns: Value Object.
"""

import base64
import binascii
import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from yakhnama.shared_kernel.errors import ValidationError
from yakhnama.shared_kernel.ids import EntityId

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
SORT_KEY_MAX_LENGTH = 256
# Upper bound on a token, checked before decoding so a huge token costs nothing. A
# 256-character sort key of plain ASCII fits comfortably, but JSON escaping (``\u0000``
# is six characters) and UTF-8 (up to four bytes per character) grow before the 4/3
# base64 expansion, so not every valid sort key fits: ``CursorPayload`` rejects any
# payload whose token would exceed this limit, so the failure happens when the cursor
# is built, never when a client sends it back.
MAX_CURSOR_LENGTH = 1024
_BASE64URL_ALPHABET = re.compile(r"[A-Za-z0-9_-]+")


class CursorDirection(StrEnum):
    """Which way a cursor continues from its position.

    Implements: Value Object.
    """

    FORWARD = "forward"
    BACKWARD = "backward"


class CursorPayload(BaseModel):
    """The decoded content of a cursor.

    Implements: Value Object.

    Attributes:
        sort_key: The last item's sort value, serialised as a string (for example an
            ISO-8601 timestamp or a name).
        last_id: The last item's id (UUIDv7), the tie-breaker for equal sort keys.
        direction: Whether the next page continues forward or backward.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sort_key: str = Field(max_length=SORT_KEY_MAX_LENGTH)
    last_id: EntityId
    direction: CursorDirection = CursorDirection.FORWARD

    @model_validator(mode="after")
    def _check_encoded_length(self) -> Self:
        if len(encode_cursor(self)) > MAX_CURSOR_LENGTH:
            message = (
                f"the encoded cursor would exceed {MAX_CURSOR_LENGTH} characters; "
                "use a shorter sort key"
            )
            raise ValueError(message)
        return self


def encode_cursor(payload: CursorPayload) -> str:
    """Return the opaque token for ``payload``.

    Args:
        payload: Where the next page starts.

    Returns:
        An unpadded base64url string, safe in a query parameter.
    """
    raw = payload.model_dump_json().encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(token: str) -> CursorPayload:
    """Parse a token produced by ``encode_cursor``.

    Args:
        token: The ``cursor`` value a client sent back.

    Returns:
        The decoded payload.

    Raises:
        ValidationError: If the token is empty, too long, not base64url, not a valid
            payload, or not in the exact canonical form ``encode_cursor`` produces.
    """
    if not 0 < len(token) <= MAX_CURSOR_LENGTH or not _BASE64URL_ALPHABET.fullmatch(
        token
    ):
        raise _invalid_cursor()
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding)
        payload = CursorPayload.model_validate_json(raw)
    except (binascii.Error, PydanticValidationError) as error:
        raise _invalid_cursor() from error
    # Different tokens can decode to the same bytes (unused trailing bits) or to JSON
    # with other key order or spacing; accepting only the canonical form makes every
    # altered token fail.
    if encode_cursor(payload) != token:
        raise _invalid_cursor()
    return payload


def _invalid_cursor() -> ValidationError:
    # The token is deliberately not echoed back: it is client input and may be huge.
    return ValidationError(
        "the pagination cursor is invalid", details={"field": "cursor"}
    )


class PageRequest(BaseModel):
    """What a client asks for: how many items and where to start.

    Implements: Value Object.

    Attributes:
        limit: Page size, 1 to ``MAX_PAGE_LIMIT``.
        cursor: Opaque token from a previous page, or ``None`` for the first page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    cursor: str | None = Field(default=None, max_length=MAX_CURSOR_LENGTH)

    def decode_cursor(self) -> CursorPayload | None:
        """Return the decoded cursor, or ``None`` on the first page.

        Returns:
            The payload the cursor carries, if any.

        Raises:
            ValidationError: If the cursor is present but invalid.
        """
        return None if self.cursor is None else decode_cursor(self.cursor)


class Page[ItemT](BaseModel):
    """One page of results.

    Implements: Value Object.

    Attributes:
        items: The results, at most ``MAX_PAGE_LIMIT``.
        next_cursor: Token for the following page, or ``None`` on the last page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ItemT, ...] = Field(max_length=MAX_PAGE_LIMIT)
    next_cursor: str | None = Field(default=None, max_length=MAX_CURSOR_LENGTH)
