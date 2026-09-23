"""In-memory fakes of the hazards ports.

The repository stages writes until the unit of work commits, exactly as a rolled-back
database transaction would leave the table unchanged. The query service reads the
repository's committed rows through the same specifications the SQL implementation
compiles, and pages by keyset on ``code``.

Patterns: Fake.
"""

from collections.abc import Iterable

from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.hazards.application.dto import (
    HazardTypeDetail,
    HazardTypeSummary,
)
from yakhnama.modules.hazards.application.queries import ListHazardTypes
from yakhnama.modules.hazards.domain.entities import HazardTaxonomy, HazardType
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    encode_cursor,
)


class InMemoryHazardTypeRepository:
    """``HazardTypeRepository`` over a dictionary keyed by code.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored hazard types, as a committed transaction left them.
    """

    def __init__(self, hazard_types: Iterable[HazardType] = ()) -> None:
        """Create the repository.

        Args:
            hazard_types: Types that exist before the test acts.
        """
        self.committed: dict[str, HazardType] = {
            hazard_type.code: hazard_type for hazard_type in hazard_types
        }
        self._staged: dict[str, HazardType] = {}

    def _current(self) -> dict[str, HazardType]:
        return {**self.committed, **self._staged}

    async def get_by_code(self, code: str) -> HazardType | None:
        """Return the hazard type with ``code``, staged changes included.

        Args:
            code: The hazard code.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(code)

    async def list_all(self) -> HazardTaxonomy:
        """Return every stored and staged hazard type as a taxonomy.

        Returns:
            The validated taxonomy.
        """
        return HazardTaxonomy.of(self._current().values())

    async def add(self, hazard_type: HazardType) -> None:
        """Stage a new hazard type.

        Args:
            hazard_type: The new aggregate.

        Raises:
            ConflictError: If the code or the id is already taken.
        """
        current = self._current()
        if hazard_type.code in current or any(
            stored.id == hazard_type.id for stored in current.values()
        ):
            message = f"hazard type {hazard_type.code!r} already exists"
            raise ConflictError(message)
        self._staged[hazard_type.code] = hazard_type

    async def save(self, hazard_type: HazardType) -> None:
        """Stage a changed hazard type.

        Args:
            hazard_type: The new state of a stored aggregate.

        Raises:
            NotFoundError: If no stored type has this code and id.
        """
        stored = self._current().get(hazard_type.code)
        if stored is None or stored.id != hazard_type.id:
            message = f"hazard type {hazard_type.code!r} is not stored"
            raise NotFoundError(message)
        self._staged[hazard_type.code] = hazard_type

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryHazardsUnitOfWork(InMemoryUnitOfWork):
    """``HazardsUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        hazard_types: The repository bound to this unit of work.
    """

    def __init__(self, hazard_types: Iterable[HazardType] = ()) -> None:
        """Create the unit of work.

        Args:
            hazard_types: Types that exist before the test acts.
        """
        super().__init__()
        self.hazard_types = InMemoryHazardTypeRepository(hazard_types)

    def _on_commit(self) -> None:
        self.hazard_types.apply_staged()

    def _on_rollback(self) -> None:
        self.hazard_types.discard_staged()


class InMemoryHazardTypeQueryService:
    """``HazardTypeQueryService`` reading a fake repository's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, repository: InMemoryHazardTypeRepository) -> None:
        """Create the query service.

        Args:
            repository: The repository whose committed rows are served.
        """
        self._repository = repository

    async def list_hazard_types(
        self, query: ListHazardTypes
    ) -> Page[HazardTypeSummary]:
        """Filter, order by code and page like the SQL implementation.

        Args:
            query: Filters and page request.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        specification = query.to_specification()
        cursor = query.page.decode_cursor()
        after = "" if cursor is None else cursor.sort_key
        matching = [
            (hazard_type, summary)
            for hazard_type in sorted(
                self._repository.committed.values(), key=lambda item: item.code
            )
            if hazard_type.code > after
            for summary in (HazardTypeSummary.from_entity(hazard_type),)
            if specification is None or specification.is_satisfied_by(summary)
        ]
        window = matching[: query.page.limit]
        next_cursor = None
        if len(matching) > query.page.limit:
            last = window[-1][0]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.code, last_id=last.id)
            )
        return Page[HazardTypeSummary](
            items=tuple(summary for _, summary in window), next_cursor=next_cursor
        )

    async def get(self, code: str) -> HazardTypeDetail | None:
        """Return the detail view of one committed hazard type.

        Args:
            code: The hazard code.

        Returns:
            The detail view, or ``None``.
        """
        hazard_type = self._repository.committed.get(code)
        return (
            None if hazard_type is None else HazardTypeDetail.from_entity(hazard_type)
        )
