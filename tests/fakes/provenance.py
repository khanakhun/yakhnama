"""In-memory fakes of the provenance ports.

The repository stages writes until the unit of work commits and enforces the
uniqueness and optimistic-concurrency rules the SQL adapter promises in
``yakhnama.modules.provenance.application.ports``. The query service reads only
committed rows through the same specifications the SQL adapter compiles.
``FakeSourceRegistrar`` and ``FakeSourceReferenceMarker`` stand in for the
provenance handlers when another module's handler is under test; they record every
command they receive.

Patterns: Fake.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from tests.fakes.clock import FrozenClock
from tests.fakes.ids import SequentialIdGenerator
from tests.fakes.uow import InMemoryUnitOfWork
from yakhnama.modules.provenance.application.commands import (
    MarkSourceReferenced,
    RegisterSource,
)
from yakhnama.modules.provenance.application.dto import SourceDetail, SourceSummary
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.factories import SourceFactory
from yakhnama.modules.provenance.domain.value_objects import SourceOwner
from yakhnama.shared_kernel.errors import ConflictError, NotFoundError
from yakhnama.shared_kernel.ids import EntityId
from yakhnama.shared_kernel.pagination import (
    CursorPayload,
    Page,
    PageRequest,
    encode_cursor,
)
from yakhnama.shared_kernel.specification import Specification

FAKE_REGISTRAR_NOW = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
"""Instant ``FakeSourceRegistrar`` stamps on the sources it registers."""


class InMemorySourceRepository:
    """``SourceRepository`` over a dictionary keyed by id.

    Implements: Fake (of Repository).

    Attributes:
        committed: The stored sources, as a committed transaction left them.
    """

    def __init__(self, sources: Iterable[Source] = ()) -> None:
        """Create the repository.

        Args:
            sources: Sources that exist before the test acts.
        """
        self.committed: dict[EntityId, Source] = {
            source.id: source for source in sources
        }
        self._staged: dict[EntityId, Source] = {}

    def _current(self) -> dict[EntityId, Source]:
        return {**self.committed, **self._staged}

    async def get(self, source_id: EntityId) -> Source | None:
        """Return the source, staged changes included.

        Args:
            source_id: The source's id.

        Returns:
            The aggregate, or ``None``.
        """
        return self._current().get(source_id)

    async def add(self, source: Source) -> None:
        """Stage a new source.

        Args:
            source: The new aggregate.

        Raises:
            ConflictError: If the id is taken.
        """
        if source.id in self._current():
            message = "the source already exists"
            raise ConflictError(message)
        self._staged[source.id] = source

    async def save(self, source: Source) -> None:
        """Stage a changed source.

        Args:
            source: The new state.

        Raises:
            NotFoundError: If the source is not stored.
            ConflictError: If the version does not follow the stored one.
        """
        stored = self._current().get(source.id)
        if stored is None:
            message = f"source {source.id} is not stored"
            raise NotFoundError(message)
        if source.version != stored.version + 1:
            message = f"source {source.id} was changed concurrently"
            raise ConflictError(message)
        self._staged[source.id] = source

    def apply_staged(self) -> None:
        """Make the staged writes permanent; called on commit."""
        self.committed.update(self._staged)
        self._staged.clear()

    def discard_staged(self) -> None:
        """Forget the staged writes; called on rollback."""
        self._staged.clear()


class InMemoryProvenanceUnitOfWork(InMemoryUnitOfWork):
    """``ProvenanceUnitOfWork`` over an in-memory repository.

    Implements: Fake (of Unit of Work).

    Attributes:
        sources: The source repository bound to this unit of work.
    """

    def __init__(self, *, sources: Iterable[Source] = ()) -> None:
        """Create the unit of work.

        Args:
            sources: Sources that exist before the test acts.
        """
        super().__init__()
        self.sources = InMemorySourceRepository(sources)

    def _on_commit(self) -> None:
        self.sources.apply_staged()

    def _on_rollback(self) -> None:
        self.sources.discard_staged()


def _newest_first(source: Source) -> tuple[datetime, EntityId]:
    return source.created_at, source.id


class InMemorySourceQueryService:
    """``SourceQueryService`` reading a fake unit of work's committed rows.

    Implements: Fake (of Query Service).
    """

    def __init__(self, uow: InMemoryProvenanceUnitOfWork) -> None:
        """Create the query service.

        Args:
            uow: The unit of work whose committed rows are served.
        """
        self._uow = uow

    async def get_source(self, source_id: EntityId) -> SourceDetail | None:
        """Return one committed source.

        Args:
            source_id: The source.

        Returns:
            The detail view, or ``None``.
        """
        source = self._uow.sources.committed.get(source_id)
        return None if source is None else SourceDetail.from_entity(source)

    async def list_sources(
        self, specification: Specification[Source], page: PageRequest
    ) -> Page[SourceSummary]:
        """Page the matching sources by ``(created_at, id)`` descending.

        Args:
            specification: The filter.
            page: Page size and cursor.

        Returns:
            One page of summaries.

        Raises:
            ValidationError: If the cursor is invalid.
        """
        cursor = page.decode_cursor()
        matches = sorted(
            (
                source
                for source in self._uow.sources.committed.values()
                if specification.is_satisfied_by(source)
            ),
            key=_newest_first,
            reverse=True,
        )
        if cursor is not None:
            before = (datetime.fromisoformat(cursor.sort_key), cursor.last_id)
            matches = [source for source in matches if _newest_first(source) < before]
        window = matches[: page.limit]
        next_cursor = None
        if len(matches) > page.limit:
            last = window[-1]
            next_cursor = encode_cursor(
                CursorPayload(sort_key=last.created_at.isoformat(), last_id=last.id)
            )
        return Page[SourceSummary](
            items=tuple(SourceSummary.from_entity(source) for source in window),
            next_cursor=next_cursor,
        )


class FakeSourceRegistrar:
    """``SourceRegistrar`` that builds sources in memory and records commands.

    It applies no policy: the real handler's policy is tested with the handler.

    Implements: Fake (of Command Handler).

    Attributes:
        commands: Every command received, in order.
        registered: Every source built, in order.
    """

    def __init__(self) -> None:
        """Create the registrar."""
        self.commands: list[RegisterSource] = []
        self.registered: list[Source] = []
        self._clock = FrozenClock(FAKE_REGISTRAR_NOW)
        self._ids = SequentialIdGenerator(seed=7001)

    async def __call__(self, command: RegisterSource) -> SourceDetail:
        """Register the source in memory.

        Args:
            command: The registration.

        Returns:
            The new source's detail view.
        """
        self.commands.append(command)
        source = (
            SourceFactory()
            .register(
                command.source_type,
                command.details,
                SourceOwner(
                    actor_id=command.actor.user_id,
                    organization_id=command.organization_id,
                ),
                clock=self._clock,
                ids=self._ids,
            )
            .state
        )
        self.registered.append(source)
        return SourceDetail.from_entity(source)


class FakeSourceReferenceMarker:
    """``SourceReferenceMarker`` that records which sources were cited.

    Implements: Fake (of Command Handler).

    Attributes:
        commands: Every command received, in order.
        registrar: Where the cited sources are looked up.
    """

    def __init__(self, registrar: FakeSourceRegistrar) -> None:
        """Create the marker.

        Args:
            registrar: The fake registrar whose sources may be cited.
        """
        self.commands: list[MarkSourceReferenced] = []
        self.registrar = registrar

    @property
    def marked_ids(self) -> tuple[EntityId, ...]:
        """Return the cited source ids, in order."""
        return tuple(command.source_id for command in self.commands)

    async def __call__(self, command: MarkSourceReferenced) -> SourceDetail:
        """Record the citation.

        Args:
            command: The source and actor.

        Returns:
            The cited source, as referenced.

        Raises:
            NotFoundError: If the registrar never registered the source.
        """
        self.commands.append(command)
        source = next(
            (
                item
                for item in self.registrar.registered
                if item.id == command.source_id
            ),
            None,
        )
        if source is None:
            message = f"source {command.source_id} was not registered"
            raise NotFoundError(message)
        return SourceDetail.from_entity(source).model_copy(
            update={"is_referenced": True}
        )
