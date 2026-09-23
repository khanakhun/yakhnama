"""SQLAlchemy adapter of the ``VerificationCaseRepository`` port.

Writes go straight to the unit of work's transaction; inserts run inside a savepoint
so a unique violation leaves the transaction usable, and rows are expunged as soon as
they are read or written. A unique violation on insert is either a taken id or a
target that already has a case (``(target_kind, target_id)`` is unique); both are
conflicts, as the port requires.

**Optimistic concurrency.** The port only promises ``NotFoundError`` on ``save``,
but two moderators moving the same case at once must not both win, so the
repository also checks versions like the other SQL repositories: it remembers the
version of every case it loaded or stored in this unit of work and updates the row
only ``WHERE version = <remembered version>`` (``version - 1`` for a case not loaded
here). A lost race raises ``ConflictError``.

There is no delete: a case, like its history, is kept for life.

Patterns: Repository (adapter side).
"""

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.verification.domain.entities import VerificationCase
from yakhnama.modules.verification.domain.errors import (
    VerificationCaseNotFoundError,
)
from yakhnama.modules.verification.domain.value_objects import VerificationTarget
from yakhnama.modules.verification.infrastructure.mappers import (
    case_to_row,
    case_to_values,
    row_to_case,
)
from yakhnama.modules.verification.infrastructure.orm import VerificationCaseRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyVerificationCaseRepository:
    """PostgreSQL-backed implementation of ``VerificationCaseRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``VerificationCaseRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, case_id: EntityId) -> VerificationCase | None:
        """Return one case.

        Args:
            case_id: The case.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._load(VerificationCaseRow.id == case_id)

    async def get_for_target(
        self, target: VerificationTarget
    ) -> VerificationCase | None:
        """Return the case of a target.

        Args:
            target: The record under verification.

        Returns:
            The aggregate, or ``None``.
        """
        return await self._load(
            VerificationCaseRow.target_kind == target.kind.value,
            VerificationCaseRow.target_id == target.target_id,
        )

    async def _load(self, *conditions: ColumnElement[bool]) -> VerificationCase | None:
        row = (
            await self._session.execute(select(VerificationCaseRow).where(*conditions))
        ).scalar_one_or_none()
        if row is None:
            return None
        self._session.expunge(row)
        case = row_to_case(row)
        self._versions[case.id] = case.version
        return case

    async def add(self, case: VerificationCase) -> None:
        """Insert a new case.

        Args:
            case: The new aggregate.

        Raises:
            ConflictError: If the id is taken or the target already has a case.
        """
        row = case_to_row(case)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = "the verification case or its target already exists"
            raise ConflictError(
                message,
                details={
                    "case_id": str(case.id),
                    "target_kind": case.target.kind.value,
                    "target_id": str(case.target.target_id),
                },
            ) from error
        self._session.expunge(row)
        self._versions[case.id] = case.version

    async def save(self, case: VerificationCase) -> None:
        """Update a stored case, checking optimistic concurrency.

        Args:
            case: The new state of a stored aggregate.

        Raises:
            VerificationCaseNotFoundError: If no case with that id is stored.
            ConflictError: If the stored version is not the one loaded here (or
                ``case.version - 1``), or ``case.version`` is not greater.
        """
        expected = self._versions.get(case.id, case.version - 1)
        statement = (
            update(VerificationCaseRow)
            .where(
                VerificationCaseRow.id == case.id,
                VerificationCaseRow.version == expected,
            )
            .values(case_to_values(case))
            .returning(VerificationCaseRow.id)
            .execution_options(synchronize_session=False)
        )
        if case.version > expected and (
            (await self._session.execute(statement)).scalar_one_or_none() is not None
        ):
            self._versions[case.id] = case.version
            return
        stored = await self._session.scalar(
            select(VerificationCaseRow.version).where(VerificationCaseRow.id == case.id)
        )
        if stored is None:
            raise VerificationCaseNotFoundError(case.id)
        message = (
            f"verification case {case.id} was changed concurrently "
            f"(expected version {expected})"
        )
        raise ConflictError(
            message,
            details={
                "case_id": str(case.id),
                "expected_version": expected,
                "stored_version": stored,
            },
        )
