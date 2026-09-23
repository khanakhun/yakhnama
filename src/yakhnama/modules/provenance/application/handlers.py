"""Write-side use cases of the provenance module.

Every handler asks its policy about ``command.actor`` before staging anything and
raises ``PermissionDeniedError`` otherwise (deny by default). A change already in
effect (the same details, a source already referenced) commits nothing and records
no event, so a retried command is harmless.

Patterns: Command Handler, Unit of Work, Policy, Domain Events.
"""

from yakhnama.modules.provenance.application.authorisation import (
    reference_policy,
    registration_policy,
    require_allowed,
    source_editor_policy,
)
from yakhnama.modules.provenance.application.commands import (
    MarkSourceReferenced,
    RegisterSource,
    UpdateSourceDetails,
)
from yakhnama.modules.provenance.application.dto import SourceDetail
from yakhnama.modules.provenance.application.ports import (
    ProvenanceUnitOfWork,
    ProvenanceUnitOfWorkFactory,
)
from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.errors import SourceNotFoundError
from yakhnama.modules.provenance.domain.factories import SourceFactory
from yakhnama.modules.provenance.domain.value_objects import SourceOwner
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.errors import PreconditionFailedError
from yakhnama.shared_kernel.ids import EntityId, IdGenerator


async def _load_source(uow: ProvenanceUnitOfWork, source_id: EntityId) -> Source:
    source = await uow.sources.get(source_id)
    if source is None:
        raise SourceNotFoundError.for_id(source_id)
    return source


def _check_version(expected: int | None, current: int) -> None:
    # Compared inside the unit of work, after the load, so the gap between the
    # client's read and this write is closed.
    if expected is not None and expected != current:
        message = "the record has changed since the client read it"
        raise PreconditionFailedError(
            message,
            details={"expected_version": expected, "current_version": current},
        )


class _ProvenanceHandler:
    """Dependencies every provenance command handler shares.

    Implements: Command Handler.
    """

    def __init__(
        self,
        uow_factory: ProvenanceUnitOfWorkFactory,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the handler.

        Args:
            uow_factory: Opens a provenance unit of work per call.
            clock: Source of timestamps and event times.
            ids: Source of aggregate and event ids.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids


class RegisterSourceHandler(_ProvenanceHandler):
    """Register a source owned by the acting user.

    Implements: Command Handler.
    """

    async def __call__(self, command: RegisterSource) -> SourceDetail:
        """Register the source at version 1.

        Args:
            command: The validated command.

        Returns:
            The new source.

        Raises:
            PermissionDeniedError: If the actor may not register a source of this
                type, or for this organisation.
        """
        require_allowed(
            registration_policy(command.source_type, command.organization_id),
            command.actor,
            action="register sources of this type",
        )
        owner = SourceOwner(
            actor_id=command.actor.user_id, organization_id=command.organization_id
        )
        async with self._uow_factory() as uow:
            source = (
                SourceFactory()
                .register(
                    command.source_type,
                    command.details,
                    owner,
                    clock=self._clock,
                    ids=self._ids,
                )
                .record_into(uow)
            )
            await uow.sources.add(source)
            await uow.commit()
        return SourceDetail.from_entity(source)


class UpdateSourceDetailsHandler(_ProvenanceHandler):
    """Replace the details of an unreferenced source; owner or moderator only.

    The policy depends on the source's owner, so the source is loaded first; nothing
    is staged before the check.

    Implements: Command Handler.
    """

    async def __call__(self, command: UpdateSourceDetails) -> SourceDetail:
        """Replace the source's details.

        Args:
            command: The validated command.

        Returns:
            The source after the change (unchanged if the details are equal).

        Raises:
            PermissionDeniedError: If the actor is neither the owner nor a
                moderator.
            SourceNotFoundError: If the source does not exist.
            PreconditionFailedError: If ``expected_version`` is stale.
            SourceImmutableError: If the source is referenced.
        """
        async with self._uow_factory() as uow:
            source = await _load_source(uow, command.source_id)
            require_allowed(
                source_editor_policy(source),
                command.actor,
                action="change this source",
            )
            _check_version(command.expected_version, source.version)
            change = source.update_details(
                command.details, clock=self._clock, ids=self._ids
            )
            if change.events:
                source = change.record_into(uow)
                await uow.sources.save(source)
            await uow.commit()
        return SourceDetail.from_entity(source)


class MarkSourceReferencedHandler(_ProvenanceHandler):
    """Freeze a source because a fact now cites it.

    Internal: bound to the ``SourceReferenceMarker`` port that reports, media,
    events and impacts receive; no endpoint routes to it. Callers invoke it right
    after committing the citing fact, in a unit of work of its own, because modules
    never share a transaction. If that second step fails the source simply stays
    mutable until the next citation marks it, which is safe: nothing is lost, and
    the citing fact stores the ``SourceRef`` either way.

    Implements: Command Handler.
    """

    async def __call__(self, command: MarkSourceReferenced) -> SourceDetail:
        """Mark the source as referenced; idempotent.

        Args:
            command: The validated command.

        Returns:
            The referenced source.

        Raises:
            PermissionDeniedError: If the actor is anonymous.
            SourceNotFoundError: If the source does not exist.
        """
        require_allowed(reference_policy(), command.actor, action="cite sources")
        # The actor is not stored on the source: who cited it is recorded by the
        # citing fact and its own events.
        async with self._uow_factory() as uow:
            source = await _load_source(uow, command.source_id)
            change = source.mark_referenced(clock=self._clock, ids=self._ids)
            if change.events:
                source = change.record_into(uow)
                await uow.sources.save(source)
            await uow.commit()
        return SourceDetail.from_entity(source)
