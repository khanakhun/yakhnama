"""In-memory fakes of the geography ports.

The repository stages writes until the unit of work commits, exactly as a rolled-back
database transaction would leave the table unchanged. The query service reads the
repository's committed rows through the same specifications the SQL implementation
compiles; it pages by keyset on ``code`` because it has no relevance ranking.

Patterns: Fake.
"""

from collections.abc import Iterable

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.geography.application.dto import PlaceDetail, PlaceSummary
from yakhnama.modules.geography.application.queries import SearchPlaces
from yakhnama.modules.geography.domain.entities import Place
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import CursorPayload, Page, encode_cursor


class InMemoryPlaceRepository:
    """``PlaceRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored places by id, as a committed transaction left them.
    """

    def __init__(self, places: Iterable[Place] = ()) -> None:
        """Create the repository.

        Args:
            places: Places that exist before the test acts.
        """
        self.committed: dict[EntityId, Place] = {place.id: place for place in places}
        self._staged: dict[EntityId, Place] = {}

    def _current(self) -> dict[EntityId, Place]:
        return {**self.committed, **self._staged}

    def committed_by_code(self) -> dict[str, Place]:
        """Return the committed places keyed by code.

        Returns:
            A new dictionary; changing it does not change the repository.
        """
        return {place.code: place for place in self.committed.values()}

    async def get(self, place_id: EntityId) -> Place | None:
        """Return the place with ``place_id``, staged changes included.

        Args:
            place_id: The place id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(place_id)

    async def get_by_code(self, code: str) -> Place | None:
        """Return the place with ``code``, staged changes included.

        Args:
            code: The place code.

        Returns:
            The aggregate, or ``None``.
        """
        return next(
            (place for place in self._current().values() if place.code == code), None
        )

    async def add(self, place: Place) -> None:
        """Stage a new place.

        Args:
            place: The new aggregate.

        Raises:
            ConflictError: If the id or the code is already taken.
        """
        current = self._current()
        if place.id in current or any(
            stored.code == place.code for stored in current.values()
        ):
            message = f"place {place.code!r} already exists"
            raise ConflictError(message)
        self._staged[place.id] = place

    async def save(self, place: Place) -> None:
        """Stage a changed place.

        Args:
            place: The new state of a stored aggregate.

        Raises:
            NotFoundError: If no stored place has this id.
        """
        if place.id not in self._current():
            message = f"place {place.code!r} is not stored"
            raise NotFoundError(message)
        self._staged[place.id] = place

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryGeographyUnitOfWork(InMemoryUnitOfWork):
    """``GeographyUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        places: The repository bound to this unit of work.
    """

    def __init__(self, places: Iterable[Place] = ()) -> None:
        """Create the unit of work.

        Args:
            places: Places that exist before the test acts.
        """
        super().__init__()
        self.places = InMemoryPlaceRepository(places)

    def _on_commit(self) -> None:
        self.places.apply_staged()

    def _on_rollback(self) -> None:
        self.places.discard_staged()


class InMemoryPlaceQueryService:
    """``PlaceQueryService`` reading a fake repository's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, repository: InMemoryPlaceRepository) -> None:
        """Create the query service.

        Args:
            repository: The repository whose committed rows are served.
        """
        self._repository = repository

    def _parent_code(self, place: Place) -> str | None:
        if place.parent_id is None:
            return None
        return self._repository.committed[place.parent_id].code

    async def search(self, query: SearchPlaces) -> Page[PlaceSummary]:
        """Filter with the query's specification, order by code and page.

        Args:
            query: Search text, filters and page request.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        specification = query.to_specification()
        cursor = query.page.decode_cursor()
        after = "" if cursor is None else cursor.sort_key
        matching = [
            place
            for place in sorted(
                self._repository.committed.values(), key=lambda item: item.code
            )
            if place.code > after and specification.is_satisfied_by(place)
        ]
        window = matching[: query.page.limit]
        next_cursor = None
        if len(matching) > query.page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        items = tuple(
            PlaceSummary.from_entity(
                place, parent_code=self._parent_code(place), language=query.language
            )
            for place in window
        )
        return Page[PlaceSummary](items=items, next_cursor=next_cursor)

    async def get(self, place_id: EntityId) -> PlaceDetail | None:
        """Return the detail view of one committed place.

        Args:
            place_id: The place id.

        Returns:
            The detail view, or ``None``.
        """
        place = self._repository.committed.get(place_id)
        if place is None:
            return None
        return PlaceDetail.from_entity(place, parent_code=self._parent_code(place))
