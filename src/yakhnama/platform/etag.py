"""Entity tags from aggregate versions and ``If-Match`` checks (RFC 9110 §8.8.3).

An aggregate's ETag is the strong tag ``"<id>:<version>"``: it changes exactly when the
aggregate's ``version`` does, and naming the id means a tag copied from another
resource never matches. Routes that return a single aggregate set it with
``set_etag``; mutating routes (``PATCH``, ``DELETE``) require ``If-Match``:

- ``require_if_match`` compares the header with the current tag and raises
  ``PreconditionRequiredError`` (428) when it is missing or
  ``PreconditionFailedError`` (412) when no listed tag matches;
- ``expected_version_from_if_match`` extracts the version the client saw, so the
  command handler can compare it with the aggregate inside its unit of work, which
  is race-free.

``If-Match`` uses the strong comparison: a weak tag (``W/"..."``) never matches, and
``*`` matches any current representation.

Patterns: none from the catalog; module-level functions only.
"""

from typing import Final
from uuid import UUID

from starlette.requests import Request
from starlette.responses import Response

from yakhnama.shared_kernel.errors import (
    PreconditionFailedError,
    PreconditionRequiredError,
)

ETAG_HEADER: Final = "ETag"
IF_MATCH_HEADER: Final = "If-Match"
ANY_TAG: Final = "*"
WEAK_PREFIX: Final = "W/"
# Versions are counters; 18 digits stays within a signed 64-bit integer.
MAX_VERSION_DIGITS: Final = 18
PRECONDITION_REQUIRED_MESSAGE: Final = (
    "This request must be conditional: send If-Match with the resource's ETag."
)
PRECONDITION_FAILED_MESSAGE: Final = (
    "The resource has changed since it was read; fetch it again and retry."
)


def make_etag(version: int, entity_id: UUID) -> str:
    """Return the strong ETag of one version of an aggregate.

    Args:
        version: The aggregate's version (non-negative).
        entity_id: The aggregate's id.

    Returns:
        The quoted tag, for example ``"0192f4c1-...:3"``.

    Raises:
        ValueError: If ``version`` is negative.
    """
    if version < 0:
        message = "version must not be negative"
        raise ValueError(message)
    return f'"{entity_id}:{version}"'


def set_etag(response: Response, etag: str) -> None:
    """Set the ``ETag`` header of a response.

    Args:
        response: The response, typically the ``Response`` parameter of a route.
        etag: A tag from ``make_etag``.
    """
    response.headers[ETAG_HEADER] = etag


def parse_if_match(header: str) -> tuple[str, ...]:
    """Split an ``If-Match`` value into its entity tags.

    Args:
        header: The raw header value, for example ``'"a:1", W/"b:2"'``.

    Returns:
        The tags as written, weak prefix kept; ``("*",)`` for ``*``.
    """
    return tuple(tag.strip() for tag in header.split(",") if tag.strip())


def if_match_matches(header: str, current_etag: str) -> bool:
    """Tell whether an ``If-Match`` value matches the current tag (strong comparison).

    Args:
        header: The raw ``If-Match`` value.
        current_etag: The resource's current tag.

    Returns:
        ``True`` for ``*`` or when a listed strong tag equals ``current_etag``.
    """
    tags = parse_if_match(header)
    return ANY_TAG in tags or current_etag in tags


def require_if_match(request: Request, current_etag: str) -> None:
    """Refuse a mutating request whose ``If-Match`` does not match.

    Args:
        request: The current request.
        current_etag: The resource's current tag, from ``make_etag``.

    Raises:
        PreconditionRequiredError: If the request has no ``If-Match``.
        PreconditionFailedError: If no listed tag matches ``current_etag``.
    """
    header = request.headers.get(IF_MATCH_HEADER)
    if header is None or not header.strip():
        raise PreconditionRequiredError(PRECONDITION_REQUIRED_MESSAGE)
    if not if_match_matches(header, current_etag):
        raise PreconditionFailedError(PRECONDITION_FAILED_MESSAGE)


def expected_version_from_if_match(header: str | None, entity_id: UUID) -> int:
    """Return the aggregate version named by ``If-Match`` for ``entity_id``.

    Use it when the version check belongs in the command handler: the route passes
    the result as the command's ``expected_version``, and the handler raises
    ``PreconditionFailedError`` if the aggregate has moved on.

    Args:
        header: The raw ``If-Match`` value, or ``None`` when absent.
        entity_id: The id of the resource being changed.

    Returns:
        The version from the single strong tag for ``entity_id``.

    Raises:
        PreconditionRequiredError: If the header is absent or blank.
        PreconditionFailedError: If the header is ``*``, lists several tags, is
            weak, or does not name ``entity_id`` with a valid version.
    """
    if header is None or not header.strip():
        raise PreconditionRequiredError(PRECONDITION_REQUIRED_MESSAGE)
    tags = parse_if_match(header)
    if len(tags) != 1 or tags[0].startswith(WEAK_PREFIX):
        raise PreconditionFailedError(PRECONDITION_FAILED_MESSAGE)
    tag = tags[0]
    prefix = f'"{entity_id}:'
    version_text = tag.removeprefix(prefix).removesuffix('"')
    if (
        not tag.startswith(prefix)
        or not tag.endswith('"')
        or not version_text.isascii()
        or not version_text.isdigit()
        or len(version_text) > MAX_VERSION_DIGITS
    ):
        raise PreconditionFailedError(PRECONDITION_FAILED_MESSAGE)
    return int(version_text)
