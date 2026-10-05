"""Registration of new sources.

Patterns: Factory.
"""

from yakhnama.modules.provenance.domain.entities import Source
from yakhnama.modules.provenance.domain.events import SourceRegistered
from yakhnama.modules.provenance.domain.value_objects import (
    SourceDetails,
    SourceOwner,
    SourceType,
)
from yakhnama.shared_kernel.clock import Clock
from yakhnama.shared_kernel.events import AggregateChange
from yakhnama.shared_kernel.ids import EntityId, IdGenerator


class SourceFactory:
    """Register sources.

    Implements: Factory.
    """

    def register(  # noqa: PLR0913  # reason: the reserved id joins the five registration inputs
        self,
        source_type: SourceType,
        details: SourceDetails,
        owner: SourceOwner,
        *,
        clock: Clock,
        ids: IdGenerator,
        source_id: EntityId | None = None,
    ) -> AggregateChange[Source]:
        """Create an unreferenced source at version 1 and ``SourceRegistered``.

        Deduplicating against existing sources (the same URL, the same citation)
        needs a repository and belongs to the command handler; the domain accepts
        duplicates because two retrievals of one page can be two sources.

        Args:
            source_type: The kind of source; fixed from now on.
            details: The descriptive fields.
            owner: Who registers it and for which organisation; ``SYSTEM_OWNER``
                for importers. Whether the actor may act for the organisation is an
                authorisation rule the handler checks with the identity policies.
            clock: Source of every timestamp.
            ids: Source of the source id (unless given) and the event id.
            source_id: The id to give the source, when the caller chose it in
                advance (a guest report reserves its source's id, ADR 0020).

        Returns:
            The new source and ``SourceRegistered``.
        """
        now = clock.now()
        source = Source.model_validate(
            {
                **details.as_fields(),
                "id": ids.new_id() if source_id is None else source_id,
                "source_type": source_type,
                "owner_actor_id": owner.actor_id,
                "organization_id": owner.organization_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        event = SourceRegistered(
            event_id=ids.new_id(),
            occurred_at=now,
            aggregate_id=source.id,
            version=source.version,
            source_type=source.source_type,
            owner_actor_id=owner.actor_id,
            organization_id=owner.organization_id,
        )
        return AggregateChange[Source](state=source, events=(event,))
