"""The ``idempotency_keys`` table (ADR 0016).

One row per ``(scope, key)``: a pending reservation while the first request runs
(``status_code`` is ``NULL``), then its stored 2xx response until ``expires_at``.
``scope`` is the SHA-256 of the principal's issuer and subject, never the subject
itself, so the table holds no identifier of a person beyond what the response body
already contains.

The Alembic migration that creates the table (``0006_idempotency_keys``, written by
the persistence-engineer) must match this model exactly; ``alembic check`` enforces
it.

Patterns: Adapter (ORM row model of ``SqlAlchemyIdempotencyStore``).
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from yakhnama.platform.db import Base
from yakhnama.platform.idempotency.store import (
    METHOD_MAX_LENGTH,
    PATH_MAX_LENGTH,
    REQUEST_HASH_LENGTH,
    SCOPE_MAX_LENGTH,
)

IDEMPOTENCY_TABLE_NAME: Final = "idempotency_keys"


class IdempotencyKeyRow(Base):
    """One ``Idempotency-Key`` reservation or stored response.

    Implements: Adapter (ORM row model of ``SqlAlchemyIdempotencyStore``).

    Attributes:
        id: Surrogate UUIDv7 primary key.
        scope: The principal's opaque scope key.
        key: The client's ``Idempotency-Key``.
        request_hash: SHA-256 hex digest of the method, path, query and body.
        method: HTTP method of the first request.
        path: Path of the first request.
        status_code: Stored 2xx status; ``NULL`` while the reservation is pending.
        response_body: Stored body bytes; ``NULL`` while pending.
        response_headers: Stored allow-listed headers as ``[[name, value], ...]``;
            ``NULL`` while pending.
        created_at: When the reservation was made (UTC).
        expires_at: When the row lapses and may be purged (UTC).
    """

    __tablename__ = IDEMPOTENCY_TABLE_NAME
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_keys_scope_key"),
        # purge_expired deletes by expires_at; without the index it scans the table.
        Index("ix_idempotency_keys_expires_at", "expires_at"),
        CheckConstraint(
            "status_code IS NULL OR (status_code >= 200 AND status_code <= 299)",
            name="status_code_success",
        ),
        CheckConstraint(
            "(status_code IS NULL) = (response_body IS NULL)"
            " AND (status_code IS NULL) = (response_headers IS NULL)",
            name="response_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(SCOPE_MAX_LENGTH))
    key: Mapped[UUID]
    request_hash: Mapped[str] = mapped_column(CHAR(REQUEST_HASH_LENGTH))
    method: Mapped[str] = mapped_column(String(METHOD_MAX_LENGTH))
    path: Mapped[str] = mapped_column(String(PATH_MAX_LENGTH))
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    # A list of [name, value] pairs rather than an object: header order and repeated
    # names survive, and the mapper validates it back into StoredResponse on read.
    response_headers: Mapped[list[list[str]] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
