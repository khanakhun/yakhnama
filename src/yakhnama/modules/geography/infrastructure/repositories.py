"""SQLAlchemy adapter of the geography repository port.

Writes go straight to the unit of work's transaction (inside a savepoint), so a
later read in the same unit of work sees them and a rollback discards them, which is
what the in-memory fake models with its staged writes. Row objects are expunged from
the session as soon as they are read or written: the database is the only state, so
an identity map holding a stale or deleted row can never shadow a later write (name
rows are deleted and re-inserted under the same deterministic ids on every save).

Optimistic concurrency: the repository remembers the ``version`` of every place it
loaded or stored in this unit of work. ``save`` updates the row only
``WHERE version = <remembered version>``; if another transaction changed the place
in between, no row matches and ``ConflictError`` is raised. A place saved without
having been loaded here is assumed to carry exactly one change (expected version
``place.version - 1``), which fails safe: more changes than that report a conflict
rather than overwrite anything.

Patterns: Repository (adapter side).
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import ReturningUpdate

from yakhnama.modules.geography.domain.entities import Place
from yakhnama.modules.geography.domain.errors import PlaceNotFoundError
from yakhnama.modules.geography.infrastructure.mappers import (
    centroid_to_element,
    geometry_to_element,
    names_to_rows,
    place_to_row,
    row_to_place,
)
from yakhnama.modules.geography.infrastructure.orm import PlaceNameRow, PlaceRow
from yakhnama.platform.db import is_unique_violation
from yakhnama.shared_kernel.errors import ConflictError
from yakhnama.shared_kernel.ids import EntityId


class SqlAlchemyPlaceRepository:
    """PostGIS-backed implementation of ``PlaceRepository``.

    The session belongs to the unit of work; this class never commits.

    Implements: Repository (port ``PlaceRepository``).
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository.

        Args:
            session: The unit of work's session.
        """
        self._session = session
        self._versions: dict[EntityId, int] = {}

    async def get(self, place_id: EntityId) -> Place | None:
        """Return the place with ``place_id``, whatever its status.

        Args:
            place_id: The place id.

        Returns:
            The aggregate, or ``None`` if it does not exist.
        """
        return await self._load(PlaceRow.id == place_id)

    async def get_by_code(self, code: str) -> Place | None:
        """Return the place with ``code``, whatever its status.

        Args:
            code: The place code.

        Returns:
            The aggregate, or ``None`` if no place has that code.
        """
        return await self._load(PlaceRow.code == code)

    async def add(self, place: Place) -> None:
        """Insert a new place and its names.

        Args:
            place: The new aggregate.

        Raises:
            ConflictError: If a place with the same id or code exists.
            sqlalchemy.exc.IntegrityError: If the parent or merge target is not
                stored, which the domain factory rules out.
        """
        try:
            # The savepoint keeps the transaction usable after a duplicate, so the
            # caller can still roll back or carry on.
            async with self._session.begin_nested():
                await self._insert((place_to_row(place),))
                await self._insert(names_to_rows(place))
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"place {place.code!r} already exists"
            raise ConflictError(message, details={"code": place.code}) from error
        self._versions[place.id] = place.version

    async def save(self, place: Place) -> None:
        """Update a stored place and replace its names.

        Args:
            place: The new state of an existing aggregate.

        Raises:
            PlaceNotFoundError: If no place with that id exists.
            ConflictError: If the stored version is not the one this unit of work
                loaded, or the new state collides with another place's code.
        """
        expected = self._versions.get(place.id, place.version - 1)
        statement = (
            update(PlaceRow)
            .where(PlaceRow.id == place.id, PlaceRow.version == expected)
            .values(
                code=place.code,
                level=place.level.value,
                parent_id=place.parent_id,
                geometry=geometry_to_element(place.geometry),
                centroid=centroid_to_element(place.centroid),
                status=place.status,
                status_reason=place.status_reason,
                merged_into_id=place.merged_into_id,
                version=place.version,
                updated_at=place.updated_at,
            )
            .returning(PlaceRow.id)
            .execution_options(synchronize_session=False)
        )
        try:
            async with self._session.begin_nested():
                await self._update(statement, place, expected)
        except IntegrityError as error:
            if not is_unique_violation(error):
                raise
            message = f"place {place.code!r} conflicts with a stored place"
            raise ConflictError(message, details={"code": place.code}) from error
        self._versions[place.id] = place.version

    async def _update(
        self, statement: ReturningUpdate[tuple[EntityId]], place: Place, expected: int
    ) -> None:
        updated = (await self._session.execute(statement)).scalar_one_or_none()
        if updated is None:
            await self._raise_missing_or_stale(place, expected)
        # Names are values of the aggregate: replace them all. Deleting first keeps
        # the unique constraints satisfied while the new set is written.
        await self._session.execute(
            delete(PlaceNameRow)
            .where(PlaceNameRow.place_id == place.id)
            .execution_options(synchronize_session=False)
        )
        await self._insert(names_to_rows(place))

    async def _insert(self, rows: Iterable[PlaceRow | PlaceNameRow]) -> None:
        staged = list(rows)
        self._session.add_all(staged)
        await self._session.flush()
        for row in staged:
            self._session.expunge(row)

    async def _load(self, condition: ColumnElement[bool]) -> Place | None:
        row = (
            await self._session.execute(select(PlaceRow).where(condition))
        ).scalar_one_or_none()
        if row is None:
            return None
        name_rows = list(
            (
                await self._session.execute(
                    select(PlaceNameRow).where(PlaceNameRow.place_id == row.id)
                )
            ).scalars()
        )
        for loaded in (row, *name_rows):
            self._session.expunge(loaded)
        place = row_to_place(row, name_rows)
        self._versions[place.id] = place.version
        return place

    async def _raise_missing_or_stale(self, place: Place, expected: int) -> None:
        stored = await self._session.scalar(
            select(PlaceRow.version).where(PlaceRow.id == place.id)
        )
        if stored is None:
            raise PlaceNotFoundError.for_id(place.id)
        message = (
            f"place {place.code!r} was changed concurrently (expected version "
            f"{expected}, stored {stored})"
        )
        raise ConflictError(
            message,
            details={"code": place.code, "expected": expected, "stored": stored},
        )
