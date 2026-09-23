"""The ``IdempotencyStore`` port and the records it exchanges (ADR 0016).

The store works in three steps so that two concurrent requests with the same key
never both run the route:

1. ``reserve`` inserts a *pending* record for ``(scope, key)``. Exactly one caller
   wins, enforced by the unique constraint; every other caller gets the existing
   record back instead.
2. The winner runs the route, then either ``complete`` stores its 2xx response and
   extends the record to the full TTL, or ``release`` deletes the reservation so the
   client may retry (a failure is not replayed).
3. ``purge_expired`` deletes records past ``expires_at``; it is run periodically.

A pending record expires after ``PENDING_LEASE``, so a process that crashes between
steps 1 and 2 blocks the key for minutes, not for the whole TTL.

Patterns: Adapter (the port), DTO (the records).
"""

from datetime import datetime, timedelta
from typing import Annotated, Final, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

PENDING_LEASE: Final = timedelta(minutes=5)
SCOPE_MAX_LENGTH: Final = 255
REQUEST_HASH_LENGTH: Final = 64
METHOD_MAX_LENGTH: Final = 10
PATH_MAX_LENGTH: Final = 2048
HEADER_VALUE_MAX_LENGTH: Final = 4096

HeaderPair = tuple[
    Annotated[str, Field(min_length=1, max_length=100)],
    Annotated[str, Field(max_length=HEADER_VALUE_MAX_LENGTH)],
]


class StoredResponse(BaseModel):
    """A successful response kept for replay.

    Implements: DTO.

    Attributes:
        status_code: The 2xx status.
        headers: The allow-listed response headers, lower-case names, in order.
        body: The exact response body bytes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status_code: int = Field(ge=200, le=299)
    headers: tuple[HeaderPair, ...] = ()
    body: bytes = b""


class IdempotencyReservation(BaseModel):
    """A request asking to own ``(scope, key)``.

    Implements: DTO.

    Attributes:
        scope: The principal's opaque scope key (``Principal.scope_key``).
        key: The client's ``Idempotency-Key``.
        request_hash: SHA-256 hex digest of the method, path, query and body.
        method: The HTTP method, for operators reading the table.
        path: The request path, for operators reading the table.
        created_at: When the reservation was made (UTC).
        expires_at: When the pending reservation lapses (UTC).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: str = Field(min_length=1, max_length=SCOPE_MAX_LENGTH)
    key: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: str = Field(min_length=1, max_length=METHOD_MAX_LENGTH)
    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    created_at: AwareDatetime
    expires_at: AwareDatetime


class IdempotencyRecord(BaseModel):
    """The live record another request already holds for ``(scope, key)``.

    Implements: DTO.

    Attributes:
        request_hash: The hash of the request that made the record.
        response: The stored response; ``None`` while that request still runs.
        expires_at: When the record lapses (UTC).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_hash: str
    response: StoredResponse | None
    expires_at: AwareDatetime


class IdempotencyStore(Protocol):
    """Durable memory of ``Idempotency-Key`` requests and their responses.

    Implements: Adapter (port side).
    """

    async def reserve(
        self, reservation: IdempotencyReservation
    ) -> IdempotencyRecord | None:
        """Claim ``(scope, key)`` for this request, atomically.

        An existing record whose ``expires_at`` is not after
        ``reservation.created_at`` counts as absent and is replaced.

        Args:
            reservation: The claim.

        Returns:
            ``None`` if this request now owns the key, otherwise the live record
            that another request holds.
        """
        ...

    async def complete(
        self, scope: str, key: UUID, response: StoredResponse, expires_at: datetime
    ) -> None:
        """Store the owner's successful response and extend the record's lifetime.

        Args:
            scope: The reservation's scope.
            key: The reservation's key.
            response: The response to replay.
            expires_at: When the stored response lapses (UTC).
        """
        ...

    async def release(self, scope: str, key: UUID) -> None:
        """Delete a pending reservation so the client may retry.

        Args:
            scope: The reservation's scope.
            key: The reservation's key.
        """
        ...

    async def purge_expired(self, now: datetime) -> int:
        """Delete every record whose ``expires_at`` is not after ``now``.

        Args:
            now: The current instant (UTC).

        Returns:
            How many records were deleted.
        """
        ...
