"""SQLAlchemy adapter of the ``SourceRepository`` port.

Writes go straight to the unit of work's transaction, so a later read in the same
unit of work sees them and a rollback discards them. Inserts run inside a savepoint
so a unique violation leaves the transaction usable. Rows are expunged as soon as
they are read or written, so a stale identity-map entry never shadows a later write.

Optimistic concurrency: ``save`` updates a row only ``WHERE version = new version -
1``. If no row matches, the id is looked up once more to tell a missing source
(``SourceNotFoundError``) from a concurrent change (``ConflictError``).

There is no delete: a source is provenance and is never removed.

Patterns: Repository (adapter side).
"""

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.modules.provenance.infrastructure.mappers import (
    row_to_source,
    source_to_row,
    source_to_values,
)
from yakhnama.modules.provenance.infrastructure.orm import SourceRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemySourceRepository:
    """PostgreSQL-backed implementation of ``SourceRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``SourceRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def get(self, source_id: EntityId) -> Source | None:
        """Return the source with ``source_id``.

        Args:
            source_id: The source's id.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(SourceRow).where(SourceRow.id == source_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        return row_to_source(row)

    async def add(self, source: Source) -> None:
        """Insert a newly registered source.

        Args:
            source: The new aggregate at version 1.

        Raises:
            ConflictError: If the id is taken.
        """
        row = source_to_row(source)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"source {source.id} already exists"
            raise ConflictError(
                message, details={"source_id": str(source.id)}
            ) from error
        self._session.expunge(row)

    async def save(self, source: Source) -> None:
        """Update a stored source, checking optimistic concurrency.

        Args:
            source: The new state; its ``version`` is one more than the stored one.

        Raises:
            SourceNotFoundError: If no source with that id exists.
            ConflictError: If the stored version is not ``source.version - 1``.
        """
        statement = (
            update(SourceRow)
            .where(SourceRow.id == source.id, SourceRow.version == source.version - 1)
            .values(source_to_values(source))
            .returning(SourceRow.id)
            .execution_options(synchronize_session=False)
        )
        if (await self._session.execute(statement)).scalar_one_or_none() is not None:
            return
        stored = await self._session.scalar(
            select(SourceRow.version).where(SourceRow.id == source.id)
        )
        if stored is None:
            raise SourceNotFoundError.for_id(source.id)
        message = (
            f"source {source.id} was changed concurrently "
            f"(expected version {source.version - 1})"
        )
        raise ConflictError(
            message,
            details={
                "source_id": str(source.id),
                "expected_version": source.version - 1,
                "stored_version": stored,
            },
        )
