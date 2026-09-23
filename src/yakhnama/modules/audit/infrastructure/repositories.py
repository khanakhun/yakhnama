"""SQLAlchemy adapter of the ``AuditEntryRepository`` port: append only.

``append`` is one statement, ``INSERT ... ON CONFLICT (event_id) DO NOTHING
RETURNING id``: the existence check and the insert cannot race, so two relays
delivering the same event concurrently store it once, and the returned row tells
whether this call inserted it. A clash on the entry's own id is not covered by the
``ON CONFLICT`` target, so it still raises a unique violation, which becomes
``ConflictError`` inside a savepoint that keeps the transaction usable.

There is deliberately no get, update or delete; the migration's trigger refuses
``UPDATE`` and ``DELETE`` on the table as well.

Patterns: Repository (adapter side).
"""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.audit.domain.entities import AuditEntry
from yakhnama.modules.audit.infrastructure.mappers import entry_to_values
from yakhnama.modules.audit.infrastructure.orm import AuditEntryRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError


class SqlAlchemyAuditEntryRepository:
    """PostgreSQL-backed implementation of ``AuditEntryRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``AuditEntryRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session

    async def append(self, entry: AuditEntry) -> bool:
        """Insert ``entry`` unless an entry for the same event already exists.

        Args:
            entry: The new entry.

        Returns:
            ``True`` if the row was inserted, ``False`` if an entry for
            ``entry.event_id`` exists and nothing was written.

        Raises:
            ConflictError: If the entry's own id is taken.
        """
        statement = (
            insert(AuditEntryRow)
            .values(entry_to_values(entry))
            .on_conflict_do_nothing(index_elements=[AuditEntryRow.event_id])
            .returning(AuditEntryRow.id)
        )
        try:
            async with self._session.begin_nested():
                inserted = (await self._session.execute(statement)).scalar_one_or_none()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"audit entry {entry.id} already exists"
            raise ConflictError(message, details={"entry_id": str(entry.id)}) from error
        return inserted is not None
