"""SQLAlchemy adapters of the ``ExportJobRepository`` and ``ImportJobRepository`` ports.

Writes go straight to the unit of work's transaction. Inserts run inside a savepoint
so a duplicate id becomes a ``ConflictError`` and leaves the transaction usable;
rows are expunged as soon as they are read or written.

**Optimistic concurrency.** The ports ask only that a saved job's version be greater
than the one loaded, and that the stored row has not changed since. Each repository
therefore remembers the version of every job it loaded or stored in this unit of
work and updates the row only ``WHERE version = <remembered version>``; a job saved
without having been loaded here is assumed to carry exactly one change (expected
``version - 1``), which fails safe. If no row matches, the id is looked up once more
to tell a missing job (``...NotFoundError``) from a concurrent change
(``ConflictError``).

There is no delete: jobs finish, they are never removed.

Patterns: Repository (adapter side).
"""

from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.exchange.domain.entities import ExportJob, ImportJob
from yakhnama.modules.exchange.domain.errors import (
    ExportJobNotFoundError,
    ImportJobNotFoundError,
)
from yakhnama.modules.exchange.infrastructure.mappers import (
    export_job_to_row,
    export_job_to_values,
    import_job_to_row,
    import_job_to_values,
    row_to_export_job,
    row_to_import_job,
)
from yakhnama.modules.exchange.infrastructure.orm import ExportJobRow, ImportJobRow
from yakhnama.platform.db import Base, is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId

# Names used in conflict messages and their details keys.
_EXPORT_JOB: Final = "export_job"
_IMPORT_JOB: Final = "import_job"


async def _insert(
    session: AsyncSession, row: Base, kind: str, job_id: EntityId
) -> None:
    """Insert ``row`` in a savepoint; a duplicate id raises ``ConflictError``."""
    try:
        # The savepoint keeps the transaction usable after a duplicate.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        if not is_unique_violation(error):
            raise
        message = f"{kind} {job_id} already exists"
        raise ConflictError(message, details={f"{kind}_id": str(job_id)}) from error
    session.expunge(row)


def _stale(kind: str, job_id: EntityId, expected: int, stored: int) -> ConflictError:
    message = f"{kind} {job_id} was changed concurrently (expected version {expected})"
    return ConflictError(
        message,
        details={
            f"{kind}_id": str(job_id),
            "expected_version": expected,
            "stored_version": stored,
        },
    )


class SqlAlchemyExportJobRepository:
    """PostgreSQL-backed implementation of ``ExportJobRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``ExportJobRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, job_id: EntityId) -> ExportJob | None:
        """Return one export job.

        Args:
            job_id: The job.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(ExportJobRow).where(ExportJobRow.id == job_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        job = row_to_export_job(row)
        self._versions[job.id] = job.version
        return job

    async def add(self, job: ExportJob) -> None:
        """Insert a new export job.

        Args:
            job: The job at version 1.

        Raises:
            ConflictError: If a job with that id exists.
        """
        await _insert(self._session, export_job_to_row(job), _EXPORT_JOB, job.id)
        self._versions[job.id] = job.version

    async def save(self, job: ExportJob) -> None:
        """Update a stored export job, checking optimistic concurrency.

        Args:
            job: The new state; its version is greater than the loaded one.

        Raises:
            ExportJobNotFoundError: If no job with that id is stored.
            ConflictError: If the stored row changed since it was loaded here, or
                the version did not grow.
        """
        expected = self._versions.get(job.id, job.version - 1)
        statement = (
            update(ExportJobRow)
            .where(ExportJobRow.id == job.id, ExportJobRow.version == expected)
            .values(export_job_to_values(job))
            .returning(ExportJobRow.id)
            .execution_options(synchronize_session=False)
        )
        # A version that did not grow is stale whatever the row holds.
        if (
            job.version > expected
            and (await self._session.execute(statement)).scalar_one_or_none()
            is not None
        ):
            self._versions[job.id] = job.version
            return
        stored = await self._session.scalar(
            select(ExportJobRow.version).where(ExportJobRow.id == job.id)
        )
        if stored is None:
            raise ExportJobNotFoundError.for_id(job.id)
        raise _stale(_EXPORT_JOB, job.id, expected, stored)


class SqlAlchemyImportJobRepository:
    """PostgreSQL-backed implementation of ``ImportJobRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``ImportJobRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, job_id: EntityId) -> ImportJob | None:
        """Return one import job.

        Args:
            job_id: The job.

        Returns:
            The aggregate, or ``None``.
        """
        row = (
            await self._session.execute(
                select(ImportJobRow).where(ImportJobRow.id == job_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        job = row_to_import_job(row)
        self._versions[job.id] = job.version
        return job

    async def add(self, job: ImportJob) -> None:
        """Insert a new import job.

        Args:
            job: The job at version 1.

        Raises:
            ConflictError: If a job with that id exists.
        """
        await _insert(self._session, import_job_to_row(job), _IMPORT_JOB, job.id)
        self._versions[job.id] = job.version

    async def save(self, job: ImportJob) -> None:
        """Update a stored import job, checking optimistic concurrency.

        Args:
            job: The new state; its version is greater than the loaded one.

        Raises:
            ImportJobNotFoundError: If no job with that id is stored.
            ConflictError: If the stored row changed since it was loaded here, or
                the version did not grow.
        """
        expected = self._versions.get(job.id, job.version - 1)
        statement = (
            update(ImportJobRow)
            .where(ImportJobRow.id == job.id, ImportJobRow.version == expected)
            .values(import_job_to_values(job))
            .returning(ImportJobRow.id)
            .execution_options(synchronize_session=False)
        )
        if (
            job.version > expected
            and (await self._session.execute(statement)).scalar_one_or_none()
            is not None
        ):
            self._versions[job.id] = job.version
            return
        stored = await self._session.scalar(
            select(ImportJobRow.version).where(ImportJobRow.id == job.id)
        )
        if stored is None:
            raise ImportJobNotFoundError.for_id(job.id)
        raise _stale(_IMPORT_JOB, job.id, expected, stored)
