"""PostgreSQL adapter of the ``IdempotencyStore`` port (ADR 0016).

``reserve`` is a single ``INSERT ... ON CONFLICT DO NOTHING RETURNING``: the unique
constraint on ``(scope, key)`` decides the winner of concurrent requests without a
lock. A loser reads the existing row; if that row has expired it is deleted and the
insert is tried once more, so a lapsed key becomes usable again.

Each call runs in its own short transaction, separate from the route's unit of work:
the reservation must be visible to a concurrent request before the route commits.

Patterns: Adapter.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yakhnama.platform.idempotency.models import IdempotencyKeyRow
from yakhnama.platform.idempotency.store import (
    IdempotencyRecord,
    IdempotencyReservation,
    StoredResponse,
)
from yakhnama.shared_kernel.ids import IdGenerator


def record_from_row(row: IdempotencyKeyRow) -> IdempotencyRecord:
    """Map a row to the port's record.

    Args:
        row: A loaded ``idempotency_keys`` row.

    Returns:
        The record; ``response`` is ``None`` for a pending reservation.
    """
    response = (
        None
        if row.status_code is None
        else StoredResponse(
            status_code=row.status_code,
            headers=tuple(
                (name, value) for name, value in (row.response_headers or [])
            ),
            body=row.response_body or b"",
        )
    )
    return IdempotencyRecord(
        request_hash=row.request_hash, response=response, expires_at=row.expires_at
    )


class SqlAlchemyIdempotencyStore:
    """``IdempotencyStore`` over the ``idempotency_keys`` table.

    Implements: Adapter.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        id_generator: IdGenerator,
    ) -> None:
        """Create the store.

        Args:
            session_factory: Opens one session per call.
            id_generator: Source of the rows' UUIDv7 ids.
        """
        self._session_factory = session_factory
        self._id_generator = id_generator

    async def reserve(
        self, reservation: IdempotencyReservation
    ) -> IdempotencyRecord | None:
        """Claim ``(scope, key)``; see ``IdempotencyStore.reserve``.

        Args:
            reservation: The claim.

        Returns:
            ``None`` if this request now owns the key, otherwise the live record.
        """
        async with self._session_factory.begin() as session:
            if await self._try_insert(session, reservation):
                return None
            existing = await self._load(session, reservation.scope, reservation.key)
            if existing is not None and existing.expires_at > reservation.created_at:
                return record_from_row(existing)
            await session.execute(
                delete(IdempotencyKeyRow).where(
                    IdempotencyKeyRow.scope == reservation.scope,
                    IdempotencyKeyRow.key == reservation.key,
                    IdempotencyKeyRow.expires_at <= reservation.created_at,
                )
            )
            if await self._try_insert(session, reservation):
                return None
            # Another request replaced the lapsed row between the delete and the
            # insert; its fresh row is the live record.
            winner = await self._load(session, reservation.scope, reservation.key)
            return None if winner is None else record_from_row(winner)

    async def complete(
        self, scope: str, key: UUID, response: StoredResponse, expires_at: datetime
    ) -> None:
        """Store the response; see ``IdempotencyStore.complete``.

        Args:
            scope: The reservation's scope.
            key: The reservation's key.
            response: The response to replay.
            expires_at: When the stored response lapses (UTC).
        """
        async with self._session_factory.begin() as session:
            await session.execute(
                update(IdempotencyKeyRow)
                .where(IdempotencyKeyRow.scope == scope, IdempotencyKeyRow.key == key)
                .values(
                    status_code=response.status_code,
                    response_body=response.body,
                    response_headers=[list(pair) for pair in response.headers],
                    expires_at=expires_at,
                )
            )

    async def release(self, scope: str, key: UUID) -> None:
        """Delete a pending reservation; see ``IdempotencyStore.release``.

        Args:
            scope: The reservation's scope.
            key: The reservation's key.
        """
        async with self._session_factory.begin() as session:
            await session.execute(
                delete(IdempotencyKeyRow).where(
                    IdempotencyKeyRow.scope == scope,
                    IdempotencyKeyRow.key == key,
                    IdempotencyKeyRow.status_code.is_(None),
                )
            )

    async def purge_expired(self, now: datetime) -> int:
        """Delete lapsed records; see ``IdempotencyStore.purge_expired``.

        Args:
            now: The current instant (UTC).

        Returns:
            How many records were deleted.
        """
        async with self._session_factory.begin() as session:
            deleted = await session.scalars(
                delete(IdempotencyKeyRow)
                .where(IdempotencyKeyRow.expires_at <= now)
                .returning(IdempotencyKeyRow.id)
            )
            return len(deleted.all())

    async def _try_insert(
        self, session: AsyncSession, reservation: IdempotencyReservation
    ) -> bool:
        statement = (
            insert(IdempotencyKeyRow)
            .values(
                id=self._id_generator.new_id(),
                scope=reservation.scope,
                key=reservation.key,
                request_hash=reservation.request_hash,
                method=reservation.method,
                path=reservation.path,
                created_at=reservation.created_at,
                expires_at=reservation.expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_keys_scope_key")
            .returning(IdempotencyKeyRow.id)
        )
        inserted = await session.scalar(statement)
        return inserted is not None

    @staticmethod
    async def _load(
        session: AsyncSession, scope: str, key: UUID
    ) -> IdempotencyKeyRow | None:
        row: IdempotencyKeyRow | None = await session.scalar(
            select(IdempotencyKeyRow).where(
                IdempotencyKeyRow.scope == scope, IdempotencyKeyRow.key == key
            )
        )
        return row
