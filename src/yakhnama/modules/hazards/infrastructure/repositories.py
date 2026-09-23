"""SQLAlchemy adapter of the hazards repository port.

Writes go straight to the unit of work's transaction inside a savepoint, and row
objects are expunged as soon as they are read or written, so the database is the
only state (see the geography repository for the same reasoning).

Optimistic concurrency: the repository remembers the ``version`` of every hazard type
it loaded (by code or through ``list_all``) or stored in this unit of work, and
``save`` updates the row only ``WHERE version = <remembered version>``. A type saved
without having been loaded here is assumed to carry exactly one change (expected
version ``version - 1``), which fails safe.

Patterns: Repository (adapter side).
"""

from typing import TYPE_CHECKING, Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.modules.hazards.domain.errors import HazardTypeNotFoundError
from yakhnama.modules.hazards.infrastructure.mappers import (
    hazard_type_to_row,
    row_to_hazard_type,
    to_json,
)
from yakhnama.modules.hazards.infrastructure.orm import HazardTypeRow
from yakhnama.shared_kernel.errors import ConflictError

if TYPE_CHECKING:
    from yakhnama.shared_kernel.ids import EntityId

UNIQUE_VIOLATION: Final = "23505"
"""PostgreSQL SQLSTATE of a unique constraint violation."""


def is_unique_violation(error: IntegrityError) -> bool:
    """Tell whether ``error`` was raised by a unique constraint or index.

    Args:
        error: The integrity error SQLAlchemy raised.

    Returns:
        ``True`` for SQLSTATE 23505; any other integrity error is a bug, not a
        conflict, and is re-raised by the callers.
    """
    return getattr(error.orig, "sqlstate", None) == UNIQUE_VIOLATION


class SqlAlchemyHazardTypeRepository:
    """PostgreSQL-backed implementation of ``HazardTypeRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``HazardTypeRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get_by_code(self, code: str) -> HazardType | None:
        """Return the hazard type with ``code``, active or retired.

        Args:
            code: The hazard code.

        Returns:
            The aggregate, or ``None`` if no type has that code.
        """
        row = (
            await self._session.execute(
                select(HazardTypeRow).where(HazardTypeRow.code == code)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return self._track(row)

    async def list_all(self) -> HazardTaxonomy:
        """Return every hazard type ever defined, retired ones included.

        Returns:
            The whole taxonomy, validated as one tree.

        Raises:
            InvalidTaxonomyError: If the stored rows do not form a consistent tree.
        """
        rows = (
            await self._session.execute(
                select(HazardTypeRow).order_by(HazardTypeRow.code)
            )
        ).scalars()
        return HazardTaxonomy.of([self._track(row) for row in rows])

    async def add(self, hazard_type: HazardType) -> None:
        """Insert a new hazard type; its parent must already be stored.

        Args:
            hazard_type: The new aggregate.

        Raises:
            ConflictError: If a hazard type with the same code or id exists.
            sqlalchemy.exc.IntegrityError: If the parent is not stored, which the
                taxonomy rules out.
        """
        row = hazard_type_to_row(hazard_type)
        try:
            # The savepoint keeps the transaction usable after a duplicate.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"hazard type {hazard_type.code!r} already exists"
            raise ConflictError(message, details={"code": hazard_type.code}) from error
        self._session.expunge(row)
        self._versions[hazard_type.id] = hazard_type.version

    async def save(self, hazard_type: HazardType) -> None:
        """Update a stored hazard type.

        Args:
            hazard_type: The new state of an existing aggregate.

        Raises:
            HazardTypeNotFoundError: If no hazard type has this id and code.
            ConflictError: If the stored version is not the one this unit of work
                loaded.
        """
        expected = self._versions.get(hazard_type.id, hazard_type.version - 1)
        statement = (
            update(HazardTypeRow)
            .where(
                HazardTypeRow.id == hazard_type.id,
                HazardTypeRow.code == hazard_type.code,
                HazardTypeRow.version == expected,
            )
            .values(
                parent_code=hazard_type.parent_code,
                labels=hazard_type.labels.model_dump(mode="json"),
                description=to_json(hazard_type.description),
                alignment=hazard_type.alignment.model_dump(mode="json"),
                attributes_schema=hazard_type.attributes_schema,
                status=hazard_type.status.value,
                retirement=to_json(hazard_type.retirement),
                version=hazard_type.version,
                updated_at=hazard_type.updated_at,
            )
            .returning(HazardTypeRow.id)
            .execution_options(synchronize_session=False)
        )
        updated = (await self._session.execute(statement)).scalar_one_or_none()
        if updated is None:
            await self._raise_missing_or_stale(hazard_type, expected)
        self._versions[hazard_type.id] = hazard_type.version

    def _track(self, row: HazardTypeRow) -> HazardType:
        self._session.expunge(row)
        hazard_type = row_to_hazard_type(row)
        self._versions[hazard_type.id] = hazard_type.version
        return hazard_type

    async def _raise_missing_or_stale(
        self, hazard_type: HazardType, expected: int
    ) -> None:
        stored = await self._session.scalar(
            select(HazardTypeRow.version).where(
                HazardTypeRow.id == hazard_type.id,
                HazardTypeRow.code == hazard_type.code,
            )
        )
        if stored is None:
            raise HazardTypeNotFoundError(hazard_type.code)
        message = (
            f"hazard type {hazard_type.code!r} was changed concurrently (expected "
            f"version {expected}, stored {stored})"
        )
        raise ConflictError(
            message,
            details={"code": hazard_type.code, "expected": expected, "stored": stored},
        )
